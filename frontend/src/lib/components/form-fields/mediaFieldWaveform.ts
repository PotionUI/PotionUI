export interface WaveformPalette {
	waveColor: string;
	progressColor: string;
	cursorColor: string;
}

export const DEFAULT_WAVEFORM_PALETTE: WaveformPalette = {
	waveColor: 'rgb(120 120 120)',
	progressColor: 'rgb(77 159 255)',
	cursorColor: 'rgb(230 230 230)'
};

export function readWaveformPalette(): WaveformPalette {
	if (typeof window === 'undefined') return DEFAULT_WAVEFORM_PALETTE;
	const styles = getComputedStyle(document.documentElement);
	const token = (name: string, fallback: string) => {
		const raw = styles.getPropertyValue(name).trim();
		return raw ? `rgb(${raw})` : fallback;
	};
	return {
		waveColor: token('--line-strong', DEFAULT_WAVEFORM_PALETTE.waveColor),
		progressColor: token('--signal', DEFAULT_WAVEFORM_PALETTE.progressColor),
		cursorColor: token('--fg', DEFAULT_WAVEFORM_PALETTE.cursorColor)
	};
}

export function waveformConfig(palette: WaveformPalette, height: number) {
	return {
		height,
		backgroundColor: 'transparent',
		barWidth: 2,
		barGap: 1,
		barRadius: 1,
		...palette
	};
}
