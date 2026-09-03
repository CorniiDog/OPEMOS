use serde::Deserialize;

pub const MAX_PROGRESS_BYTES: u64 = 64 * 1024;
pub const MAX_SEQUENCE: u64 = 1_000_000_000;

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Progress {
    pub schema_version: u8,
    pub sequence: u64,
    pub status: Status,
    pub phase: Phase,
    pub completed: Option<u64>,
    pub total: Option<u64>,
    #[serde(default)]
    pub step_completed: Option<u64>,
    #[serde(default)]
    pub step_total: Option<u64>,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum Status {
    Working,
    Succeeded,
    Failed,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Starting,
    Inspecting,
    WaitingForNetwork,
    Downloading,
    Verifying,
    Building,
    InstallingUserspace,
    InstallingModules,
    UpdatingBoot,
    GeneratingInitramfs,
    CleaningUp,
    Complete,
    RecoveryRequired,
}

impl Phase {
    pub fn label(self) -> &'static str {
        match self {
            Self::Starting => "STARTING OPEMOS",
            Self::Inspecting => "CHECKING EXACT NVIDIA SUPPORT",
            Self::WaitingForNetwork => "WAITING FOR A TRUSTED NETWORK",
            Self::Downloading => "DOWNLOADING AUTHENTICATED PAYLOADS",
            Self::Verifying => "VERIFYING SIGNATURES AND HASHES",
            Self::Building => "BUILDING FOR THE EXACT KERNEL",
            Self::InstallingUserspace => "INSTALLING NVIDIA USERSPACE",
            Self::InstallingModules => "INSTALLING NVIDIA MODULES",
            Self::UpdatingBoot => "UPDATING BOOT CONFIGURATION",
            Self::GeneratingInitramfs => "GENERATING INITRAMFS",
            Self::CleaningUp => "VERIFYING AND CLEANING UP",
            Self::Complete => "NVIDIA GRAPHICS READY",
            Self::RecoveryRequired => "RECOVERY NEEDS ATTENTION",
        }
    }
}

impl Progress {
    pub fn parse(input: &[u8]) -> Result<Self, &'static str> {
        if input.is_empty() || input.len() as u64 > MAX_PROGRESS_BYTES {
            return Err("progress document is empty or excessive");
        }
        let value: Self =
            serde_json::from_slice(input).map_err(|_| "progress document is malformed")?;
        value.validate()?;
        Ok(value)
    }

    pub fn validate(&self) -> Result<(), &'static str> {
        if self.schema_version != 1 {
            return Err("progress schema is unsupported");
        }
        if self.sequence > MAX_SEQUENCE {
            return Err("progress sequence is excessive");
        }
        validate_counter_pair(
            self.completed,
            self.total,
            "progress counters are inconsistent",
        )?;
        validate_counter_pair(
            self.step_completed,
            self.step_total,
            "step progress counters are inconsistent",
        )?;
        if self.status == Status::Succeeded
            && (self.phase != Phase::Complete || self.completed != self.total)
        {
            return Err("successful progress is not complete");
        }
        if self.status == Status::Failed && self.phase != Phase::RecoveryRequired {
            return Err("failed progress must require recovery");
        }
        if self.status == Status::Working
            && matches!(self.phase, Phase::Complete | Phase::RecoveryRequired)
        {
            return Err("working progress uses a terminal phase");
        }
        Ok(())
    }

    pub fn fraction(&self) -> Option<f32> {
        fraction(self.completed, self.total)
    }

    pub fn step_fraction(&self) -> Option<f32> {
        fraction(self.step_completed, self.step_total)
    }
}

fn validate_counter_pair(
    completed: Option<u64>,
    total: Option<u64>,
    error: &'static str,
) -> Result<(), &'static str> {
    match (completed, total) {
        (None, None) => Ok(()),
        (Some(completed), Some(total))
            if total > 0 && total <= MAX_SEQUENCE && completed <= total =>
        {
            Ok(())
        }
        _ => Err(error),
    }
}

fn fraction(completed: Option<u64>, total: Option<u64>) -> Option<f32> {
    match (completed, total) {
        (Some(completed), Some(total)) if total > 0 => Some(completed as f32 / total as f32),
        _ => None,
    }
}

#[derive(Clone, Debug)]
pub struct ProgressTracker {
    current: Progress,
}

pub const CANVAS: u32 = 0x00101924;
pub const PANEL: u32 = 0x00182535;
pub const PANEL_EDGE: u32 = 0x00344c62;
pub const TEXT: u32 = 0x00edf5fb;
pub const MUTED: u32 = 0x009fb4c5;
pub const STEAM_BLUE: u32 = 0x0066c0f4;
pub const NVIDIA_GREEN: u32 = 0x0076b900;
pub const FAILURE_RED: u32 = 0x00e06c75;

pub struct Frame<'a> {
    pub width: u32,
    pub height: u32,
    pub pixels: &'a mut [u32],
}

impl Frame<'_> {
    pub fn clear(&mut self, color: u32) {
        self.pixels.fill(color);
    }

    pub fn rect(&mut self, x: u32, y: u32, width: u32, height: u32, color: u32) {
        let x_end = x.saturating_add(width).min(self.width);
        let y_end = y.saturating_add(height).min(self.height);
        for row in y.min(self.height)..y_end {
            let start = (row * self.width + x.min(self.width)) as usize;
            let end = (row * self.width + x_end) as usize;
            self.pixels[start..end].fill(color);
        }
    }

    pub fn text(&mut self, x: u32, y: u32, value: &str, scale: u32, color: u32) {
        let mut cursor = x;
        for character in value.chars() {
            let glyph = glyph(character.to_ascii_uppercase());
            for (row, bits) in glyph.iter().enumerate() {
                for column in 0..5 {
                    if bits & (1 << (4 - column)) != 0 {
                        self.rect(
                            cursor + column * scale,
                            y + row as u32 * scale,
                            scale,
                            scale,
                            color,
                        );
                    }
                }
            }
            cursor = cursor.saturating_add(6 * scale);
        }
    }

    pub fn gradient_pill(&mut self, x: u32, y: u32, width: u32, height: u32) {
        if width < 4 || height < 4 {
            return;
        }
        let radius = height / 2;
        let inset = (height / 16).max(1);
        for local_y in 0..height {
            for local_x in 0..width {
                if !inside_pill(local_x, local_y, width, height, radius) {
                    continue;
                }
                let mut color = pill_gradient(local_x, width);
                let inside_inner = local_x >= inset
                    && local_y >= inset
                    && local_x + inset < width
                    && local_y + inset < height
                    && inside_pill(
                        local_x - inset,
                        local_y - inset,
                        width - inset * 2,
                        height - inset * 2,
                        radius.saturating_sub(inset),
                    );
                if !inside_inner {
                    color = blend(color, 0x00ffffff, 46);
                }
                self.rect(x + local_x, y + local_y, 1, 1, color);
            }
        }
    }
}

pub fn render(frame: &mut Frame<'_>, progress: &Progress, pulse: f32) {
    frame.clear(CANVAS);
    let margin = (frame.width / 16).max(20);
    let card_width = frame.width.saturating_sub(margin * 2);
    let card_height = (frame.height.saturating_mul(3) / 5)
        .max(220)
        .min(frame.height.saturating_sub(margin * 2));
    let card_y = frame.height.saturating_sub(card_height) / 2;
    frame.rect(margin, card_y, card_width, card_height, PANEL_EDGE);
    frame.rect(
        margin + 2,
        card_y + 2,
        card_width.saturating_sub(4),
        card_height.saturating_sub(4),
        PANEL,
    );

    let scale = if frame.width >= 1200 {
        4
    } else if frame.width >= 700 {
        3
    } else {
        2
    };
    let left = margin + (card_width / 12).max(18);
    let top = card_y + (card_height / 7).max(22);
    frame.gradient_pill(left, top, 24 * scale, 6 * scale);
    frame.text(left + 28 * scale, top, "OPEMOS", scale, TEXT);

    let phase_y = top + 15 * scale;
    frame.text(left, phase_y, progress.phase.label(), scale, TEXT);
    frame.text(
        left,
        phase_y + 10 * scale,
        "EXACT-KERNEL NVIDIA RECOVERY",
        scale.max(2) - 1,
        MUTED,
    );

    let bar_x = left;
    let bar_y = card_y + card_height.saturating_sub((card_height / 5).max(52));
    let bar_width = card_width.saturating_sub((left - margin) * 2);
    let bar_height = (frame.height / 110).clamp(5, 10);
    let bar_gap = (bar_height / 2).max(3);
    let color = if progress.status == Status::Failed {
        FAILURE_RED
    } else {
        STEAM_BLUE
    };
    draw_progress_bar(
        frame,
        bar_x,
        bar_y,
        bar_width,
        bar_height,
        color,
        progress.fraction(),
        pulse,
        4,
    );
    let step_color = if progress.status == Status::Failed {
        FAILURE_RED
    } else {
        NVIDIA_GREEN
    };
    draw_progress_bar(
        frame,
        bar_x,
        bar_y + bar_height + bar_gap,
        bar_width,
        bar_height,
        step_color,
        progress.step_fraction(),
        (pulse + 0.17).fract(),
        5,
    );
}

#[allow(clippy::too_many_arguments)]
fn draw_progress_bar(
    frame: &mut Frame<'_>,
    x: u32,
    y: u32,
    width: u32,
    height: u32,
    color: u32,
    progress: Option<f32>,
    pulse: f32,
    indeterminate_divisor: u32,
) {
    frame.rect(x, y, width, height, PANEL_EDGE);
    let inner_width = width.saturating_sub(4);
    let inner_height = height.saturating_sub(4);
    if let Some(fraction) = progress {
        let filled = (inner_width as f32 * fraction.clamp(0.0, 1.0)) as u32;
        frame.rect(x + 2, y + 2, filled, inner_height, color);
    } else {
        let segment = (width / indeterminate_divisor.max(1))
            .max(8)
            .min(inner_width);
        let travel = inner_width.saturating_sub(segment);
        let offset = (travel as f32 * pulse.clamp(0.0, 1.0)) as u32;
        frame.rect(x + 2 + offset, y + 2, segment, inner_height, color);
    }
}

fn inside_pill(x: u32, y: u32, width: u32, height: u32, radius: u32) -> bool {
    if x >= radius && x < width.saturating_sub(radius) {
        return true;
    }
    let center_x = if x < radius {
        radius.saturating_sub(1)
    } else {
        width.saturating_sub(radius)
    };
    let center_y = height / 2;
    let dx = i64::from(x) - i64::from(center_x);
    let dy = i64::from(y) - i64::from(center_y);
    dx * dx + dy * dy <= i64::from(radius) * i64::from(radius)
}

fn pill_gradient(x: u32, width: u32) -> u32 {
    let position = x as f32 / width.saturating_sub(1).max(1) as f32;
    if position <= 0.48 {
        interpolate(0x001a9fff, 0x000875b5, position / 0.48)
    } else {
        interpolate(0x000875b5, 0x0076b900, (position - 0.48) / 0.52)
    }
}

fn interpolate(first: u32, second: u32, amount: f32) -> u32 {
    let channel = |shift: u32| {
        let start = ((first >> shift) & 0xffu32) as f32;
        let end = ((second >> shift) & 0xffu32) as f32;
        (start + (end - start) * amount.clamp(0.0, 1.0)).round() as u32
    };
    (channel(16) << 16) | (channel(8) << 8) | channel(0)
}

fn blend(first: u32, second: u32, second_alpha: u32) -> u32 {
    let channel = |shift: u32| {
        let first = (first >> shift) & 0xffu32;
        let second = (second >> shift) & 0xffu32;
        (first * (255 - second_alpha) + second * second_alpha) / 255
    };
    (channel(16) << 16) | (channel(8) << 8) | channel(0)
}

fn glyph(character: char) -> [u8; 7] {
    match character {
        'A' => [14, 17, 17, 31, 17, 17, 17],
        'B' => [30, 17, 17, 30, 17, 17, 30],
        'C' => [14, 17, 16, 16, 16, 17, 14],
        'D' => [30, 17, 17, 17, 17, 17, 30],
        'E' => [31, 16, 16, 30, 16, 16, 31],
        'F' => [31, 16, 16, 30, 16, 16, 16],
        'G' => [14, 17, 16, 23, 17, 17, 14],
        'H' => [17, 17, 17, 31, 17, 17, 17],
        'I' => [31, 4, 4, 4, 4, 4, 31],
        'J' => [7, 2, 2, 2, 18, 18, 12],
        'K' => [17, 18, 20, 24, 20, 18, 17],
        'L' => [16, 16, 16, 16, 16, 16, 31],
        'M' => [17, 27, 21, 21, 17, 17, 17],
        'N' => [17, 25, 21, 19, 17, 17, 17],
        'O' => [14, 17, 17, 17, 17, 17, 14],
        'P' => [30, 17, 17, 30, 16, 16, 16],
        'Q' => [14, 17, 17, 17, 21, 18, 13],
        'R' => [30, 17, 17, 30, 20, 18, 17],
        'S' => [15, 16, 16, 14, 1, 1, 30],
        'T' => [31, 4, 4, 4, 4, 4, 4],
        'U' => [17, 17, 17, 17, 17, 17, 14],
        'V' => [17, 17, 17, 17, 17, 10, 4],
        'W' => [17, 17, 17, 21, 21, 21, 10],
        'X' => [17, 17, 10, 4, 10, 17, 17],
        'Y' => [17, 17, 10, 4, 4, 4, 4],
        'Z' => [31, 1, 2, 4, 8, 16, 31],
        '0' => [14, 17, 19, 21, 25, 17, 14],
        '1' => [4, 12, 4, 4, 4, 4, 14],
        '2' => [14, 17, 1, 2, 4, 8, 31],
        '3' => [30, 1, 1, 14, 1, 1, 30],
        '4' => [2, 6, 10, 18, 31, 2, 2],
        '5' => [31, 16, 16, 30, 1, 1, 30],
        '6' => [14, 16, 16, 30, 17, 17, 14],
        '7' => [31, 1, 2, 4, 8, 8, 8],
        '8' => [14, 17, 17, 14, 17, 17, 14],
        '9' => [14, 17, 17, 15, 1, 1, 14],
        '-' => [0, 0, 0, 31, 0, 0, 0],
        '.' => [0, 0, 0, 0, 0, 12, 12],
        '/' => [1, 1, 2, 4, 8, 16, 16],
        ':' => [0, 4, 4, 0, 4, 4, 0],
        _ => [0; 7],
    }
}

impl ProgressTracker {
    pub fn new(current: Progress) -> Self {
        Self { current }
    }

    pub fn update(&mut self, next: Progress) -> Result<(), &'static str> {
        if next.sequence < self.current.sequence {
            return Err("progress sequence regressed");
        }
        if self.current.status != Status::Working && next != self.current {
            return Err("terminal progress changed");
        }
        if next.sequence == self.current.sequence && next != self.current {
            return Err("progress sequence was reused");
        }
        if let ((Some(previous), Some(previous_total)), (Some(completed), Some(total))) = (
            (self.current.completed, self.current.total),
            (next.completed, next.total),
        ) {
            let previous_scaled = u128::from(previous) * u128::from(total);
            let next_scaled = u128::from(completed) * u128::from(previous_total);
            if next_scaled < previous_scaled {
                return Err("progress completion regressed");
            }
        }
        if next.phase == self.current.phase {
            if let ((Some(previous), Some(previous_total)), (Some(completed), Some(total))) = (
                (self.current.step_completed, self.current.step_total),
                (next.step_completed, next.step_total),
            ) {
                let previous_scaled = u128::from(previous) * u128::from(total);
                let next_scaled = u128::from(completed) * u128::from(previous_total);
                if next_scaled < previous_scaled {
                    return Err("step progress completion regressed");
                }
            }
        }
        self.current = next;
        Ok(())
    }

    pub fn current(&self) -> &Progress {
        &self.current
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn value(
        sequence: u64,
        status: Status,
        phase: Phase,
        completed: Option<u64>,
        total: Option<u64>,
    ) -> Progress {
        Progress {
            schema_version: 1,
            sequence,
            status,
            phase,
            completed,
            total,
            step_completed: None,
            step_total: None,
        }
    }

    #[test]
    fn parses_bounded_progress() {
        let value = Progress::parse(br#"{"schemaVersion":1,"sequence":4,"status":"working","phase":"verifying","completed":2,"total":5}"#).unwrap();
        assert_eq!(value.phase, Phase::Verifying);
        assert_eq!(value.fraction(), Some(0.4));
        assert_eq!(value.step_fraction(), None);
    }

    #[test]
    fn accepts_complete_step_pair_and_rejects_partial_or_excessive_step_progress() {
        let value = Progress::parse(br#"{"schemaVersion":1,"sequence":4,"status":"working","phase":"verifying","completed":2,"total":5,"stepCompleted":7,"stepTotal":10}"#).unwrap();
        assert_eq!(value.step_fraction(), Some(0.7));
        assert!(Progress::parse(br#"{"schemaVersion":1,"sequence":4,"status":"working","phase":"verifying","completed":2,"total":5,"stepCompleted":7}"#).is_err());
        assert!(Progress::parse(br#"{"schemaVersion":1,"sequence":4,"status":"working","phase":"verifying","completed":2,"total":5,"stepCompleted":1,"stepTotal":1000000001}"#).is_err());
    }

    #[test]
    fn rejects_unknown_fields_phases_and_excessive_documents() {
        assert!(Progress::parse(br#"{"schemaVersion":1,"sequence":1,"status":"working","phase":"shell","completed":null,"total":null}"#).is_err());
        assert!(Progress::parse(br#"{"schemaVersion":1,"sequence":1,"status":"working","phase":"starting","completed":null,"total":null,"message":"untrusted"}"#).is_err());
        assert!(Progress::parse(&vec![b' '; MAX_PROGRESS_BYTES as usize + 1]).is_err());
    }

    #[test]
    fn rejects_invalid_terminal_and_counter_states() {
        assert!(
            value(1, Status::Succeeded, Phase::Verifying, Some(1), Some(1))
                .validate()
                .is_err()
        );
        assert!(value(1, Status::Failed, Phase::Complete, None, None)
            .validate()
            .is_err());
        assert!(value(
            1,
            Status::Working,
            Phase::InstallingModules,
            Some(3),
            Some(2)
        )
        .validate()
        .is_err());
    }

    #[test]
    fn rejects_regression_reuse_and_terminal_replacement() {
        let mut tracker =
            ProgressTracker::new(value(2, Status::Working, Phase::Verifying, None, None));
        assert!(tracker
            .update(value(1, Status::Working, Phase::Building, None, None))
            .is_err());
        assert!(tracker
            .update(value(2, Status::Working, Phase::Building, None, None))
            .is_err());
        tracker
            .update(value(
                3,
                Status::Succeeded,
                Phase::Complete,
                Some(1),
                Some(1),
            ))
            .unwrap();
        assert!(tracker
            .update(value(
                4,
                Status::Failed,
                Phase::RecoveryRequired,
                None,
                None
            ))
            .is_err());
    }

    #[test]
    fn rejects_fraction_regression_even_when_totals_change() {
        let mut tracker = ProgressTracker::new(value(
            2,
            Status::Working,
            Phase::Downloading,
            Some(3),
            Some(4),
        ));
        assert!(tracker
            .update(value(
                3,
                Status::Working,
                Phase::Verifying,
                Some(7),
                Some(10),
            ))
            .is_err());
    }

    #[test]
    fn step_progress_may_reset_only_when_phase_changes() {
        let mut current = value(2, Status::Working, Phase::Downloading, Some(2), Some(10));
        current.step_completed = Some(8);
        current.step_total = Some(10);
        let mut tracker = ProgressTracker::new(current);
        let mut regressed = value(3, Status::Working, Phase::Downloading, Some(3), Some(10));
        regressed.step_completed = Some(1);
        regressed.step_total = Some(10);
        assert_eq!(
            tracker.update(regressed).unwrap_err(),
            "step progress completion regressed"
        );
        let mut next_phase = value(4, Status::Working, Phase::Verifying, Some(4), Some(10));
        next_phase.step_completed = Some(1);
        next_phase.step_total = Some(10);
        tracker.update(next_phase).unwrap();
    }

    #[test]
    fn renderer_is_bounded_and_draws_expected_palette() {
        let mut pixels = vec![0; 640 * 400];
        let progress = value(
            3,
            Status::Working,
            Phase::InstallingModules,
            Some(2),
            Some(5),
        );
        render(
            &mut Frame {
                width: 640,
                height: 400,
                pixels: &mut pixels,
            },
            &progress,
            0.5,
        );
        assert!(pixels.contains(&CANVAS));
        assert!(pixels.contains(&PANEL));
        assert!(pixels
            .iter()
            .any(|pixel| (*pixel & 0x0000ff00) > 0x00007000));
        assert!(pixels.contains(&STEAM_BLUE));
        assert!(pixels.contains(&NVIDIA_GREEN));
    }

    #[test]
    fn renderer_handles_tiny_bounded_frames() {
        let progress = value(0, Status::Working, Phase::Starting, None, None);
        for (width, height) in [(1, 1), (16, 9), (64, 48), (320, 200)] {
            let mut pixels = vec![0; width * height];
            render(
                &mut Frame {
                    width: width as u32,
                    height: height as u32,
                    pixels: &mut pixels,
                },
                &progress,
                1.0,
            );
            assert_eq!(pixels.len(), width * height);
        }
    }

    #[test]
    fn renderer_uses_failure_palette_for_terminal_recovery() {
        let mut pixels = vec![0; 640 * 400];
        let progress = value(9, Status::Failed, Phase::RecoveryRequired, None, None);
        render(
            &mut Frame {
                width: 640,
                height: 400,
                pixels: &mut pixels,
            },
            &progress,
            0.5,
        );
        assert!(pixels.contains(&FAILURE_RED));
    }

    #[test]
    fn pill_gradient_matches_the_canonical_svg_endpoints() {
        assert_eq!(pill_gradient(0, 192), 0x001a9fff);
        assert_eq!(pill_gradient(191, 192), 0x0076b900);
        assert_eq!(pill_gradient(48, 101), 0x000875b5);
    }
}
