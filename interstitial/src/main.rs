#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("opemos-interstitial supports SteamOS and Arch Linux only");
    std::process::exit(2);
}

#[cfg(target_os = "linux")]
mod linux {
    use drm::buffer::{Buffer, DrmFourcc};
    use drm::control::dumbbuffer::DumbBuffer;
    use drm::control::{connector, crtc, framebuffer, Device as ControlDevice, Mode};
    use drm::Device;
    use opemos_interstitial::{
        render, Frame, Phase, Progress, ProgressTracker, Status, MAX_PROGRESS_BYTES,
    };
    use std::ffi::{OsStr, OsString};
    use std::fs::{File, OpenOptions};
    use std::io::{self, Read};
    use std::os::fd::{AsFd, BorrowedFd};
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::thread;
    use std::time::{Duration, Instant};

    const PRODUCTION_PROGRESS: &str = "/run/opemos/interstitial/progress.json";
    const MAX_PIXELS: u64 = 33_554_432;
    const MAX_TIMEOUT_SECONDS: u64 = 7200;
    static TERMINATED: AtomicBool = AtomicBool::new(false);

    extern "C" fn stop(_: libc::c_int) {
        TERMINATED.store(true, Ordering::Release);
    }

    fn install_signal_handlers() -> io::Result<()> {
        let handler = stop as *const () as libc::sighandler_t;
        for signal in [libc::SIGINT, libc::SIGTERM] {
            if unsafe { libc::signal(signal, handler) } == libc::SIG_ERR {
                return Err(io::Error::last_os_error());
            }
        }
        Ok(())
    }

    #[derive(Debug)]
    struct Card(File);

    impl AsFd for Card {
        fn as_fd(&self) -> BorrowedFd<'_> {
            self.0.as_fd()
        }
    }
    impl Device for Card {}
    impl ControlDevice for Card {}

    struct Display {
        card: Card,
        driver: OsString,
        prior_connectors: Vec<connector::Handle>,
        crtc: crtc::Info,
        framebuffer: Option<framebuffer::Handle>,
        buffer: Option<DumbBuffer>,
        width: u32,
        height: u32,
        pixels: Vec<u32>,
    }

    impl Display {
        fn open() -> io::Result<Self> {
            let mut failures = Vec::new();
            for priority in 0..DISPLAY_DRIVER_PRIORITIES.len() {
                for index in 0..16 {
                    let path = format!("/dev/dri/card{index}");
                    match Self::open_card(&path, priority) {
                        Ok(Some(display)) => return Ok(display),
                        Ok(None) => {}
                        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                        Err(error) => {
                            let failure = format!("{path}: {error}");
                            if !failures.contains(&failure) {
                                failures.push(failure);
                            }
                        }
                    }
                }
            }
            let message = if failures.is_empty() {
                "no DRM card nodes were available".to_owned()
            } else {
                format!("no usable DRM/KMS display; {}", failures.join("; "))
            };
            Err(io::Error::new(io::ErrorKind::NotFound, message))
        }

        fn open_card(path: &str, priority: usize) -> io::Result<Option<Self>> {
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
                .open(path)?;
            let card = Card(file);
            let driver = card.get_driver()?;
            let driver_name = driver.name().to_os_string();
            match display_driver_priority(&driver_name) {
                Some(actual) if actual != priority => return Ok(None),
                Some(_) => {}
                None if priority == 0 => {
                    return Err(io::Error::new(
                        io::ErrorKind::Unsupported,
                        format!("unsupported DRM driver {}", driver_name.to_string_lossy()),
                    ))
                }
                None => return Ok(None),
            }
            if card.get_driver_capability(drm::DriverCapability::DumbBuffer)? == 0 {
                return Err(io::Error::new(
                    io::ErrorKind::Unsupported,
                    "DRM device has no dumb-buffer support",
                ));
            }
            let resources = card.resource_handles()?;
            for handle in resources.connectors() {
                let info = match card.get_connector(*handle, true) {
                    Ok(info) if info.state() == connector::State::Connected => info,
                    _ => continue,
                };
                let mode = match info.modes().iter().copied().find(valid_mode) {
                    Some(mode) => mode,
                    None => continue,
                };
                let crtc_handle = info
                    .current_encoder()
                    .and_then(|encoder| card.get_encoder(encoder).ok())
                    .and_then(|encoder| encoder.crtc())
                    .or_else(|| {
                        info.encoders().iter().find_map(|encoder| {
                            let encoder = card.get_encoder(*encoder).ok()?;
                            resources
                                .filter_crtcs(encoder.possible_crtcs())
                                .first()
                                .copied()
                        })
                    });
                let crtc = match crtc_handle.and_then(|handle| card.get_crtc(handle).ok()) {
                    Some(crtc) => crtc,
                    None => continue,
                };
                let mut prior_connectors: Vec<_> = resources
                    .connectors()
                    .iter()
                    .filter_map(|candidate| {
                        let candidate_info = card.get_connector(*candidate, false).ok()?;
                        let encoder = candidate_info.current_encoder()?;
                        let encoder_info = card.get_encoder(encoder).ok()?;
                        (encoder_info.crtc() == Some(crtc.handle())).then_some(*candidate)
                    })
                    .collect();
                if crtc.mode().is_some() && prior_connectors.is_empty() {
                    prior_connectors.push(info.handle());
                }
                return Self::configure(
                    card,
                    driver_name,
                    info.handle(),
                    prior_connectors,
                    crtc,
                    mode,
                )
                .and_then(|mut display| {
                    // A backend is usable only after the first complete frame
                    // reaches its mapped scanout buffer without error.
                    display.draw(&starting(), 0.0)?;
                    Ok(Some(display))
                });
            }
            Err(io::Error::new(
                io::ErrorKind::NotFound,
                "DRM device has no connected bounded display mode",
            ))
        }

        fn configure(
            card: Card,
            driver: OsString,
            connector: connector::Handle,
            prior_connectors: Vec<connector::Handle>,
            crtc: crtc::Info,
            mode: Mode,
        ) -> io::Result<Self> {
            let (width16, height16) = mode.size();
            let (width, height) = (u32::from(width16), u32::from(height16));
            let mut buffer = card.create_dumb_buffer((width, height), DrmFourcc::Xrgb8888, 32)?;
            let framebuffer = card.add_framebuffer(&buffer, 24, 32)?;
            let pixels = vec![0; (u64::from(width) * u64::from(height)) as usize];
            card.set_crtc(
                crtc.handle(),
                Some(framebuffer),
                (0, 0),
                &[connector],
                Some(mode),
            )?;
            // Clear the newly visible buffer immediately; later frames replace it.
            card.map_dumb_buffer(&mut buffer)?.as_mut().fill(0);
            Ok(Self {
                card,
                driver,
                prior_connectors,
                crtc,
                framebuffer: Some(framebuffer),
                buffer: Some(buffer),
                width,
                height,
                pixels,
            })
        }

        fn draw(&mut self, progress: &Progress, pulse: f32) -> io::Result<()> {
            render(
                &mut Frame {
                    width: self.width,
                    height: self.height,
                    pixels: &mut self.pixels,
                },
                progress,
                pulse,
            );
            let buffer = self
                .buffer
                .as_mut()
                .ok_or_else(|| io::Error::other("DRM buffer is unavailable"))?;
            let pitch = buffer.pitch() as usize;
            let mut mapping = self.card.map_dumb_buffer(buffer)?;
            let destination = mapping.as_mut();
            let row_bytes = self.width as usize * 4;
            if pitch < row_bytes || destination.len() < pitch.saturating_mul(self.height as usize) {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "DRM mapping has an invalid pitch or size",
                ));
            }
            for row in 0..self.height as usize {
                let target = &mut destination[row * pitch..row * pitch + row_bytes];
                let source =
                    &self.pixels[row * self.width as usize..(row + 1) * self.width as usize];
                for (index, pixel) in source.iter().enumerate() {
                    let offset = index * 4;
                    target[offset..offset + 4].copy_from_slice(&pixel.to_ne_bytes());
                }
            }
            Ok(())
        }

        fn driver_name(&self) -> &OsStr {
            &self.driver
        }
    }

    const DISPLAY_DRIVER_PRIORITIES: [&str; 7] = [
        "nvidia-drm",
        "i915",
        "xe",
        "amdgpu",
        "nouveau",
        "simpledrm",
        "virtio_gpu",
    ];

    fn display_driver_priority(name: &OsStr) -> Option<usize> {
        DISPLAY_DRIVER_PRIORITIES
            .iter()
            .position(|candidate| name == OsStr::new(candidate))
    }

    impl Drop for Display {
        fn drop(&mut self) {
            let connectors = if self.crtc.mode().is_some() {
                self.prior_connectors.as_slice()
            } else {
                &[][..]
            };
            let _ = self.card.set_crtc(
                self.crtc.handle(),
                self.crtc.framebuffer(),
                self.crtc.position(),
                connectors,
                self.crtc.mode(),
            );
            if let Some(framebuffer) = self.framebuffer.take() {
                let _ = self.card.destroy_framebuffer(framebuffer);
            }
            if let Some(buffer) = self.buffer.take() {
                let _ = self.card.destroy_dumb_buffer(buffer);
            }
        }
    }

    fn valid_mode(mode: &Mode) -> bool {
        let (width, height) = mode.size();
        width > 0
            && height > 0
            && width <= 8192
            && height <= 8192
            && u64::from(width) * u64::from(height) <= MAX_PIXELS
    }

    fn read_progress(path: &Path, development: bool) -> Result<Progress, &'static str> {
        let mut options = OpenOptions::new();
        options
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW);
        let mut file = options
            .open(path)
            .map_err(|_| "progress document is unavailable")?;
        let metadata = file
            .metadata()
            .map_err(|_| "progress metadata is unavailable")?;
        if !metadata.is_file() || metadata.len() == 0 || metadata.len() > MAX_PROGRESS_BYTES {
            return Err("progress document is unsafe or excessive");
        }
        if !development && (metadata.uid() != 0 || metadata.mode() & 0o022 != 0) {
            return Err("progress document ownership or permissions are unsafe");
        }
        let mut bytes = Vec::with_capacity(metadata.len() as usize);
        file.by_ref()
            .take(MAX_PROGRESS_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| "progress document could not be read")?;
        if bytes.len() as u64 != metadata.len() {
            return Err("progress document changed while it was read");
        }
        Progress::parse(&bytes)
    }

    struct Options {
        progress: PathBuf,
        timeout: Duration,
        smoke_test: bool,
        development: bool,
    }

    fn options() -> Result<Options, &'static str> {
        let mut progress = PathBuf::from(PRODUCTION_PROGRESS);
        let mut timeout = Duration::from_secs(300);
        let mut smoke_test = false;
        let mut development = false;
        let mut arguments = std::env::args().skip(1);
        while let Some(argument) = arguments.next() {
            match argument.as_str() {
                "--development-mode" => development = true,
                "--progress" => {
                    progress = PathBuf::from(arguments.next().ok_or("--progress requires a path")?)
                }
                "--timeout" => {
                    let seconds: u64 = arguments
                        .next()
                        .ok_or("--timeout requires seconds")?
                        .parse()
                        .map_err(|_| "--timeout is invalid")?;
                    if !(1..=MAX_TIMEOUT_SECONDS).contains(&seconds) {
                        return Err("--timeout must be between 1 and 7200 seconds");
                    }
                    timeout = Duration::from_secs(seconds);
                }
                "--smoke-test" => smoke_test = true,
                "-h" | "--help" => {
                    println!("Usage: opemos-interstitial [--timeout SECONDS] [--smoke-test]\n       opemos-interstitial --development-mode --progress FILE [--timeout SECONDS]");
                    std::process::exit(0);
                }
                _ => return Err("unknown argument"),
            }
        }
        if progress != Path::new(PRODUCTION_PROGRESS) && !development {
            return Err("custom progress paths require --development-mode");
        }
        if development && unsafe { libc::geteuid() } == 0 {
            return Err("development mode is refused for root");
        }
        Ok(Options {
            progress,
            timeout,
            smoke_test,
            development,
        })
    }

    fn starting() -> Progress {
        Progress {
            schema_version: 1,
            sequence: 0,
            status: Status::Working,
            phase: Phase::Starting,
            completed: None,
            total: None,
            step_completed: None,
            step_total: None,
        }
    }

    fn terminal_releases_display(status: Status, elapsed: Duration) -> bool {
        status == Status::Succeeded && elapsed >= Duration::from_millis(1500)
    }

    pub fn main() -> Result<(), String> {
        let options = options().map_err(str::to_owned)?;
        if options.smoke_test {
            let mut pixels = vec![0; 800 * 480];
            render(
                &mut Frame {
                    width: 800,
                    height: 480,
                    pixels: &mut pixels,
                },
                &starting(),
                0.5,
            );
            if pixels.iter().all(|pixel| *pixel == 0) {
                return Err("smoke-test frame was empty".into());
            }
            println!(
                "{{\"schemaVersion\":1,\"status\":\"passed\",\"renderer\":\"software-frame\"}}"
            );
            return Ok(());
        }
        install_signal_handlers()
            .map_err(|error| format!("could not install signal handlers: {error}"))?;
        let display_deadline = Instant::now() + Duration::from_secs(10);
        let mut display = loop {
            match Display::open() {
                Ok(display) => break display,
                Err(error)
                    if Instant::now() < display_deadline && !TERMINATED.load(Ordering::Acquire) =>
                {
                    eprintln!("opemos-interstitial: waiting for DRM/KMS display: {error}");
                    thread::sleep(Duration::from_millis(250));
                }
                Err(error) => {
                    return Err(format!(
                        "DRM/KMS display unavailable; continuing boot without graphics: {error}"
                    ))
                }
            }
        };
        eprintln!(
            "opemos-interstitial: rendering with DRM driver {}",
            display.driver_name().to_string_lossy()
        );
        let started = Instant::now();
        let mut tracker = ProgressTracker::new(starting());
        let mut last_sequence = 0;
        let mut terminal_at = None;
        let mut last_read_warning = None;
        while !TERMINATED.load(Ordering::Acquire) {
            if started.elapsed() >= options.timeout {
                return Err("interstitial watchdog expired; continuing boot".into());
            }
            match read_progress(&options.progress, options.development) {
                Ok(next) if next.sequence > last_sequence => {
                    tracker.update(next).map_err(str::to_owned)?;
                    last_sequence = tracker.current().sequence;
                    if tracker.current().status != Status::Working {
                        terminal_at = Some(Instant::now());
                    }
                }
                Ok(_) => {}
                Err(error) if started.elapsed() < Duration::from_secs(5) => {
                    if last_read_warning
                        .is_none_or(|time: Instant| time.elapsed() >= Duration::from_secs(1))
                    {
                        eprintln!("opemos-interstitial: {error}");
                        last_read_warning = Some(Instant::now());
                    }
                }
                Err(error) => return Err(format!("{error}; continuing boot")),
            }
            let cycle = (started.elapsed().as_millis() % 1800) as f32 / 1800.0;
            let pulse = if cycle <= 0.5 {
                cycle * 2.0
            } else {
                (1.0 - cycle) * 2.0
            };
            display
                .draw(tracker.current(), pulse)
                .map_err(|error| format!("DRM/KMS rendering failed: {error}"))?;
            // A failed attempt remains visible until the bounded watchdog or
            // the next attempt explicitly stops this renderer. Releasing its
            // scanout immediately would restore the stale boot framebuffer.
            if terminal_at.is_some_and(|time| {
                terminal_releases_display(tracker.current().status, time.elapsed())
            }) {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(100));
        }
        Ok(())
    }

    #[cfg(test)]
    mod tests {
        #[test]
        fn failed_recovery_keeps_scanout_until_watchdog_or_next_attempt() {
            use super::{terminal_releases_display, Duration, Status};
            assert!(!terminal_releases_display(
                Status::Failed,
                Duration::from_secs(7200)
            ));
            assert!(!terminal_releases_display(
                Status::Succeeded,
                Duration::from_secs(1)
            ));
            assert!(terminal_releases_display(
                Status::Succeeded,
                Duration::from_secs(2)
            ));
        }

        use super::display_driver_priority;
        use std::ffi::OsStr;

        #[test]
        fn recovery_display_orders_checked_native_and_firmware_backends() {
            for (priority, driver) in [
                "nvidia-drm",
                "i915",
                "xe",
                "amdgpu",
                "nouveau",
                "simpledrm",
                "virtio_gpu",
            ]
            .iter()
            .enumerate()
            {
                assert_eq!(display_driver_priority(OsStr::new(driver)), Some(priority));
            }
            assert_eq!(display_driver_priority(OsStr::new("vboxvideo")), None);
            assert_eq!(display_driver_priority(OsStr::new("")), None);
        }
    }
}

#[cfg(target_os = "linux")]
fn main() {
    if let Err(message) = linux::main() {
        eprintln!("opemos-interstitial: {message}");
        std::process::exit(1);
    }
}
