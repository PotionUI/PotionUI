export interface EditorTheme {
	surface2: string;
	surface3: string;
	line: string;
	fg: string;
	canvas: string;
}

function token(style: CSSStyleDeclaration, name: string, fallback: string): string {
	const raw = style.getPropertyValue(name).trim();
	return raw ? `rgb(${raw})` : fallback;
}

export function readTheme(): EditorTheme {
	const style = getComputedStyle(document.documentElement);
	return {
		surface2: token(style, '--surface-2', 'rgb(40 40 40)'),
		surface3: token(style, '--surface-3', 'rgb(52 52 52)'),
		line: token(style, '--line-strong', 'rgb(70 70 70)'),
		fg: token(style, '--fg', 'rgb(230 230 230)'),
		canvas: token(style, '--canvas', 'rgb(12 12 12)')
	};
}
