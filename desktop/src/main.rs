use std::{
    collections::BTreeSet,
    io::Read,
    path::PathBuf,
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicI8, Ordering},
        mpsc::{self, Receiver},
        Arc,
    },
    time::Duration,
};

use eframe::egui::{self, Align, Color32, FontFamily, FontId, Layout, RichText, Stroke, Vec2};
use serde::Deserialize;

const DEFAULT_RECOVERYCTL: &str =
    "/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/bootstrap/recoveryctl.sh";
const MAX_STATUS_BYTES: usize = 256 * 1024;
const EXPECTED_MODULES: [&str; 5] = [
    "nvidia",
    "nvidia_drm",
    "nvidia_modeset",
    "nvidia_peermem",
    "nvidia_uvm",
];
const CANVAS: Color32 = Color32::from_rgb(11, 17, 24);
const SURFACE: Color32 = Color32::from_rgba_premultiplied(25, 44, 60, 238);
const BORDER: Color32 = Color32::from_rgba_premultiplied(184, 220, 241, 42);
const TEXT: Color32 = Color32::from_rgb(245, 247, 251);
const MUTED: Color32 = Color32::from_rgb(170, 180, 197);
const STEAM_BLUE: Color32 = Color32::from_rgb(102, 192, 244);
const NVIDIA_GREEN: Color32 = Color32::from_rgb(155, 211, 90);
const WARNING: Color32 = Color32::from_rgb(242, 212, 119);
const FAILURE: Color32 = Color32::from_rgb(255, 148, 148);

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Target {
    kernel_version: String,
    nvidia_version: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ModuleRecord {
    name: String,
    present: bool,
    #[serde(default)]
    vermagic: String,
    #[serde(default)]
    version: String,
    #[serde(default)]
    exact_kernel: bool,
    #[serde(default)]
    exact_userspace: bool,
}

#[derive(Clone, Debug, Deserialize)]
struct ModuleVerification {
    status: String,
    records: Vec<ModuleRecord>,
}

#[derive(Clone, Debug, Deserialize)]
struct Fallback {
    active: bool,
    profile: Option<String>,
    #[serde(rename = "automaticProfile")]
    automatic_profile: String,
    #[serde(rename = "nouveauAutomatic")]
    nouveau_automatic: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RecoveryStatus {
    schema_version: u32,
    status: String,
    reason: String,
    target: Target,
    module_verification: ModuleVerification,
    fallback: Fallback,
    actions: Vec<String>,
}

impl RecoveryStatus {
    fn validate(self) -> Result<Self, String> {
        if self.schema_version != 1 {
            return Err("The installed guardian returned an unsupported status schema.".into());
        }
        if !matches!(
            self.status.as_str(),
            "healthy" | "recovery-required" | "fallback-active"
        ) || !safe_token(&self.reason)
        {
            return Err("The installed guardian returned an invalid status identity.".into());
        }
        if !safe_kernel(&self.target.kernel_version)
            || self
                .target
                .nvidia_version
                .as_deref()
                .is_some_and(|value| !safe_version(value))
        {
            return Err("The installed guardian returned an invalid target identity.".into());
        }
        if !matches!(
            self.module_verification.status.as_str(),
            "verified" | "failed"
        ) || self.module_verification.records.len() != EXPECTED_MODULES.len()
        {
            return Err("The installed guardian returned an incomplete module result.".into());
        }
        let names = self
            .module_verification
            .records
            .iter()
            .map(|record| record.name.as_str())
            .collect::<BTreeSet<_>>();
        if names != EXPECTED_MODULES.into_iter().collect()
            || self.module_verification.records.iter().any(|record| {
                record.vermagic.len() > 192
                    || record.version.len() > 64
                    || (!record.vermagic.is_empty() && !safe_kernel(&record.vermagic))
                    || (!record.version.is_empty() && !safe_version(&record.version))
            })
        {
            return Err("The installed guardian returned invalid module records.".into());
        }
        let exact_modules = self.module_verification.records.iter().all(|record| {
            record.present
                && record.exact_kernel
                && record.vermagic == self.target.kernel_version
                && record.exact_userspace
                && self.target.nvidia_version.as_deref() == Some(record.version.as_str())
        });
        if (self.module_verification.status == "verified") != exact_modules
            || (self.status == "healthy" && (!exact_modules || self.fallback.active))
            || (self.status == "recovery-required" && exact_modules)
            || (self.status == "fallback-active") != self.fallback.active
        {
            return Err("The installed guardian returned an inconsistent recovery status.".into());
        }
        let allowed_actions = [
            "disable-fallback",
            "enable-console-fallback",
            "repair-exact-kernel",
            "coordinate-ab-rollback",
        ];
        let unique_actions = self.actions.iter().collect::<BTreeSet<_>>();
        if self.actions.len() > allowed_actions.len()
            || unique_actions.len() != self.actions.len()
            || self
                .actions
                .iter()
                .any(|action| !allowed_actions.contains(&action.as_str()))
            || self.fallback.profile.as_deref().is_some_and(|value| {
                !matches!(value, "console" | "igpu-desktop" | "nouveau-experimental")
            })
            || self.fallback.active != self.fallback.profile.is_some()
            || self.fallback.automatic_profile != "console"
            || self.fallback.nouveau_automatic
        {
            return Err("The installed guardian returned an unsafe recovery policy.".into());
        }
        Ok(self)
    }
}

fn safe_token(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-' || byte == b'_'
        })
}

fn safe_kernel(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 192
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'+' | b'-'))
}

fn safe_version(value: &str) -> bool {
    let components = value.split('.').collect::<Vec<_>>();
    value.len() <= 64
        && matches!(components.len(), 2 | 3)
        && components.iter().all(|component| {
            !component.is_empty() && component.bytes().all(|byte| byte.is_ascii_digit())
        })
}

fn parse_status(bytes: &[u8]) -> Result<RecoveryStatus, String> {
    if bytes.is_empty() || bytes.len() > MAX_STATUS_BYTES {
        return Err("The recovery status response was empty or exceeded its size limit.".into());
    }
    serde_json::from_slice::<RecoveryStatus>(bytes)
        .map_err(|_| "The installed guardian returned malformed status JSON.".to_owned())?
        .validate()
}

fn inspect(recoveryctl: PathBuf) -> Result<RecoveryStatus, String> {
    let mut child = Command::new("/usr/bin/timeout")
        .args(["--signal=TERM", "--kill-after=2s", "30s"])
        .arg(&recoveryctl)
        .args(["status", "--json"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "Could not launch the installed OPEMOS recovery guardian.".to_owned())?;
    let mut output = Vec::new();
    child
        .stdout
        .take()
        .ok_or_else(|| "Could not capture the recovery status response.".to_owned())?
        .take((MAX_STATUS_BYTES + 1) as u64)
        .read_to_end(&mut output)
        .map_err(|_| "Could not read the recovery status response.".to_owned())?;
    if output.len() > MAX_STATUS_BYTES {
        // Do not leave a producer that exceeded the bounded contract blocked on
        // its full stdout pipe until the outer timeout expires.
        let _ = child.kill();
        let _ = child.wait();
        return Err("The recovery status response exceeded its size limit.".into());
    }
    let status = child
        .wait()
        .map_err(|_| "Could not wait for recovery inspection.".to_owned())?;
    if !status.success() {
        return Err(if status.code() == Some(124) {
            "Recovery inspection timed out after 30 seconds.".into()
        } else {
            "The recovery guardian could not determine a trusted status.".into()
        });
    }
    parse_status(&output)
}

enum ViewState {
    Loading,
    Ready(Box<RecoveryStatus>),
    Failed(String),
}

struct StatusApp {
    recoveryctl: PathBuf,
    state: ViewState,
    receiver: Option<Receiver<Result<RecoveryStatus, String>>>,
    smoke_test: bool,
    rendered_frames: u8,
    smoke_outcome: Arc<AtomicI8>,
}

impl StatusApp {
    fn new(recoveryctl: PathBuf, smoke_test: bool, smoke_outcome: Arc<AtomicI8>) -> Self {
        let mut app = Self {
            recoveryctl,
            state: ViewState::Loading,
            receiver: None,
            smoke_test,
            rendered_frames: 0,
            smoke_outcome,
        };
        app.refresh();
        app
    }

    fn refresh(&mut self) {
        if self.receiver.is_some() {
            return;
        }
        self.state = ViewState::Loading;
        let recoveryctl = self.recoveryctl.clone();
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let _ = sender.send(inspect(recoveryctl));
        });
        self.receiver = Some(receiver);
    }

    fn poll(&mut self, context: &egui::Context) {
        let Some(receiver) = &self.receiver else {
            return;
        };
        match receiver.try_recv() {
            Ok(Ok(status)) => {
                self.state = ViewState::Ready(Box::new(status));
                self.smoke_outcome.store(1, Ordering::Release);
                self.receiver = None;
            }
            Ok(Err(message)) => {
                self.state = ViewState::Failed(message);
                self.smoke_outcome.store(-1, Ordering::Release);
                self.receiver = None;
            }
            Err(mpsc::TryRecvError::Empty) => {
                context.request_repaint_after(Duration::from_millis(80));
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                self.state = ViewState::Failed("Recovery inspection ended unexpectedly.".into());
                self.smoke_outcome.store(-1, Ordering::Release);
                self.receiver = None;
            }
        }
    }
}

impl eframe::App for StatusApp {
    fn update(&mut self, context: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll(context);
        egui::CentralPanel::default()
            .frame(egui::Frame::new().fill(CANVAS).inner_margin(28.0))
            .show(context, |ui| {
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.vertical(|ui| {
                                ui.label(
                                    RichText::new("STEAMOS NVIDIA RECOVERY")
                                        .font(FontId::new(12.0, FontFamily::Proportional))
                                        .strong()
                                        .color(STEAM_BLUE),
                                );
                                ui.add_space(4.0);
                                ui.heading(
                                    RichText::new("OPEMOS system status").size(29.0).color(TEXT),
                                );
                                ui.label(
                                    RichText::new(
                                        "Exact-kernel health from the installed boot guardian.",
                                    )
                                    .size(14.0)
                                    .color(MUTED),
                                );
                            });
                            ui.with_layout(Layout::right_to_left(Align::TOP), |ui| {
                                if ui
                                    .add_enabled(
                                        self.receiver.is_none(),
                                        egui::Button::new("Refresh"),
                                    )
                                    .clicked()
                                {
                                    self.refresh();
                                }
                            });
                        });
                        ui.add_space(20.0);

                        match &self.state {
                            ViewState::Loading => status_card(
                                ui,
                                "Inspecting installed system",
                                "Reading the bounded schema-1 guardian contract.",
                                WARNING,
                            ),
                            ViewState::Failed(message) => {
                                status_card(ui, "Status unavailable", message, FAILURE)
                            }
                            ViewState::Ready(status) => draw_status(ui, status),
                        }
                    });
            });

        if self.smoke_test {
            self.rendered_frames = self.rendered_frames.saturating_add(1);
            if self.rendered_frames >= 3 && self.receiver.is_none() {
                context.send_viewport_cmd(egui::ViewportCommand::Close);
            } else {
                context.request_repaint_after(Duration::from_millis(50));
            }
        }
    }
}

fn card() -> egui::Frame {
    egui::Frame::new()
        .fill(SURFACE)
        .stroke(Stroke::new(1.0_f32, BORDER))
        .corner_radius(16.0)
        .inner_margin(16.0)
}

fn status_card(ui: &mut egui::Ui, title: &str, message: &str, color: Color32) {
    card().show(ui, |ui| {
        ui.heading(RichText::new(title).size(18.0).color(TEXT));
        ui.add_space(5.0);
        ui.label(RichText::new(message).size(13.0).color(color));
    });
}

fn draw_status(ui: &mut egui::Ui, status: &RecoveryStatus) {
    let (title, message, color) = match status.status.as_str() {
        "healthy" => (
            "Exact NVIDIA stack ready",
            "All five modules match the running kernel and userspace.",
            NVIDIA_GREEN,
        ),
        "fallback-active" => (
            "Recovery profile active",
            "The system is using its fail-safe recovery configuration.",
            WARNING,
        ),
        _ => (
            "Recovery action required",
            "The active slot cannot start the exact NVIDIA stack safely.",
            FAILURE,
        ),
    };
    status_card(ui, title, message, color);
    ui.add_space(12.0);

    ui.columns(2, |columns| {
        card().show(&mut columns[0], |ui| {
            ui.label(
                RichText::new("ACTIVE TARGET")
                    .size(11.0)
                    .strong()
                    .color(STEAM_BLUE),
            );
            ui.add_space(7.0);
            ui.label(RichText::new("Kernel").color(MUTED));
            ui.label(
                RichText::new(&status.target.kernel_version)
                    .monospace()
                    .color(TEXT),
            );
            ui.add_space(7.0);
            ui.label(RichText::new("NVIDIA userspace").color(MUTED));
            ui.label(
                RichText::new(
                    status
                        .target
                        .nvidia_version
                        .as_deref()
                        .unwrap_or("Not detected"),
                )
                .monospace()
                .color(TEXT),
            );
        });
        card().show(&mut columns[1], |ui| {
            ui.label(
                RichText::new("RECOVERY POLICY")
                    .size(11.0)
                    .strong()
                    .color(STEAM_BLUE),
            );
            ui.add_space(7.0);
            ui.label(RichText::new("Fallback").color(MUTED));
            ui.label(
                RichText::new(if status.fallback.active {
                    status.fallback.profile.as_deref().unwrap_or("active")
                } else {
                    "inactive"
                })
                .color(TEXT),
            );
            ui.add_space(7.0);
            ui.label(RichText::new("Recommended actions").color(MUTED));
            ui.label(
                RichText::new(if status.actions.is_empty() {
                    "No action required".to_owned()
                } else {
                    status
                        .actions
                        .iter()
                        .map(|action| friendly_action(action))
                        .collect::<Vec<_>>()
                        .join(" · ")
                })
                .color(TEXT),
            );
        });
    });
    ui.add_space(12.0);

    card().show(ui, |ui| {
        ui.label(
            RichText::new("KERNEL MODULES")
                .size(11.0)
                .strong()
                .color(STEAM_BLUE),
        );
        ui.add_space(8.0);
        for record in &status.module_verification.records {
            ui.horizontal(|ui| {
                let verified = record.present && record.exact_kernel && record.exact_userspace;
                ui.label(RichText::new("●").color(if verified { NVIDIA_GREEN } else { FAILURE }));
                ui.label(RichText::new(&record.name).monospace().color(TEXT));
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    ui.label(
                        RichText::new(if verified {
                            "Exact match"
                        } else if record.present {
                            "Mismatch"
                        } else {
                            "Missing"
                        })
                        .size(12.0)
                        .color(if verified {
                            NVIDIA_GREEN
                        } else {
                            FAILURE
                        }),
                    );
                });
            });
            ui.add_space(4.0);
        }
    });
    ui.add_space(12.0);
    ui.label(
        RichText::new("This companion is read-only. Run recoveryctl from Konsole for authenticated repair or fallback changes.")
            .size(12.0)
            .color(MUTED),
    );
}

fn friendly_action(action: &str) -> &'static str {
    match action {
        "disable-fallback" => "Disable fallback after verification",
        "enable-console-fallback" => "Enable console fallback",
        "repair-exact-kernel" => "Repair exact kernel",
        "coordinate-ab-rollback" => "Review A/B rollback",
        _ => "Unsupported action",
    }
}

struct Options {
    recoveryctl: PathBuf,
    smoke_test: bool,
}

fn arguments() -> Result<Options, String> {
    let mut recoveryctl = PathBuf::from(DEFAULT_RECOVERYCTL);
    let mut smoke_test = false;
    let mut arguments = std::env::args().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--recoveryctl" => {
                recoveryctl = arguments
                    .next()
                    .map(PathBuf::from)
                    .ok_or_else(|| "--recoveryctl requires a path".to_owned())?;
            }
            "--smoke-test" => smoke_test = true,
            "-h" | "--help" => {
                println!("Usage: opemos-recovery-status [--recoveryctl PATH] [--smoke-test]");
                std::process::exit(0);
            }
            _ => return Err(format!("unknown argument: {argument}")),
        }
    }
    Ok(Options {
        recoveryctl,
        smoke_test,
    })
}

fn main() -> eframe::Result {
    if !cfg!(target_os = "linux") {
        eprintln!("opemos-recovery-status supports SteamOS and Arch Linux only");
        std::process::exit(2);
    }
    let options = arguments().unwrap_or_else(|message| {
        eprintln!("opemos-recovery-status: {message}");
        std::process::exit(2);
    });
    let smoke_test = options.smoke_test;
    let viewport = egui::ViewportBuilder::default()
        .with_title("OPEMOS System Status")
        .with_inner_size(Vec2::new(720.0, 640.0))
        .with_min_inner_size(Vec2::new(640.0, 560.0));
    let smoke_outcome = Arc::new(AtomicI8::new(0));
    let app_outcome = Arc::clone(&smoke_outcome);
    eframe::run_native(
        "OPEMOS System Status",
        eframe::NativeOptions {
            viewport,
            renderer: eframe::Renderer::Glow,
            ..Default::default()
        },
        Box::new(move |_context| {
            Ok(Box::new(StatusApp::new(
                options.recoveryctl,
                options.smoke_test,
                app_outcome,
            )))
        }),
    )?;
    if smoke_test && smoke_outcome.load(Ordering::Acquire) != 1 {
        eprintln!("opemos-recovery-status: smoke test did not render a valid status");
        std::process::exit(1);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_document() -> Vec<u8> {
        let records = EXPECTED_MODULES
            .iter()
            .map(|name| {
                serde_json::json!({
                    "name": name,
                    "present": true,
                    "vermagic": "6.16.12-valve24.4-1-neptune-616-gfixture",
                    "version": "575.64.05",
                    "exactKernel": true,
                    "exactUserspace": true
                })
            })
            .collect::<Vec<_>>();
        serde_json::to_vec(&serde_json::json!({
            "schemaVersion": 1,
            "status": "healthy",
            "reason": "exact_nvidia_ready",
            "target": {
                "kernelVersion": "6.16.12-valve24.4-1-neptune-616-gfixture",
                "nvidiaVersion": "575.64.05"
            },
            "moduleVerification": {"status": "verified", "records": records},
            "fallback": {
                "active": false,
                "profile": null,
                "automaticProfile": "console",
                "profiles": ["console", "igpu-desktop", "nouveau-experimental"],
                "nouveauAutomatic": false
            },
            "actions": [],
            "futureAdditiveField": true
        }))
        .unwrap()
    }

    #[test]
    fn accepts_exact_schema_one_status_and_additive_fields() {
        let status = parse_status(&valid_document()).unwrap();
        assert_eq!(status.status, "healthy");
        assert_eq!(status.module_verification.records.len(), 5);
    }

    #[test]
    fn rejects_malformed_excessive_and_incomplete_status() {
        assert!(parse_status(b"").is_err());
        assert!(parse_status(b"not-json").is_err());
        assert!(parse_status(&vec![b' '; MAX_STATUS_BYTES + 1]).is_err());
        let mut document: serde_json::Value = serde_json::from_slice(&valid_document()).unwrap();
        document["moduleVerification"]["records"] = serde_json::json!([]);
        assert!(parse_status(&serde_json::to_vec(&document).unwrap()).is_err());
    }

    #[test]
    fn rejects_duplicate_module_identity_and_automatic_nouveau() {
        let mut document: serde_json::Value = serde_json::from_slice(&valid_document()).unwrap();
        document["moduleVerification"]["records"][0]["name"] = serde_json::json!("nvidia_uvm");
        assert!(parse_status(&serde_json::to_vec(&document).unwrap()).is_err());
        let mut document: serde_json::Value = serde_json::from_slice(&valid_document()).unwrap();
        document["fallback"]["nouveauAutomatic"] = serde_json::json!(true);
        assert!(parse_status(&serde_json::to_vec(&document).unwrap()).is_err());
    }

    #[test]
    fn rejects_inconsistent_status_actions_profiles_and_versions() {
        for mutate in [
            |document: &mut serde_json::Value| {
                document["fallback"]["active"] = serde_json::json!(true)
            },
            |document: &mut serde_json::Value| {
                document["actions"] = serde_json::json!(["run-anything"])
            },
            |document: &mut serde_json::Value| {
                document["target"]["nvidiaVersion"] = serde_json::json!("575..05")
            },
            |document: &mut serde_json::Value| {
                document["target"]["nvidiaVersion"] = serde_json::json!("580.10.01")
            },
            |document: &mut serde_json::Value| {
                document["moduleVerification"]["records"][0]["vermagic"] =
                    serde_json::json!("6.16.12-wrong-kernel")
            },
        ] {
            let mut document: serde_json::Value =
                serde_json::from_slice(&valid_document()).unwrap();
            mutate(&mut document);
            assert!(parse_status(&serde_json::to_vec(&document).unwrap()).is_err());
        }
    }
}
