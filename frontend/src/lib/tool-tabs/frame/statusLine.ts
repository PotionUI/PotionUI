export interface StatusSize {
	width: number;
	height: number;
}

export interface StatusInput {
	tool: string | null;
	areaTool: boolean;
	size: StatusSize | null;
	marked: StatusSize | null;
	output: StatusSize | null;
}

export interface StatusParts {
	size: string | null;
	extra: string | null;
}

const OUTPUT_TOOLS: ReadonlySet<string> = new Set(['crop', 'resize']);

export function formatSize(size: StatusSize | null): string | null {
	if (!size || !(size.width > 0) || !(size.height > 0)) return null;
	return `${Math.round(size.width)}×${Math.round(size.height)}`;
}

export function statusParts(input: StatusInput): StatusParts {
	const size = formatSize(input.size);
	let extra: string | null = null;
	if (input.tool && OUTPUT_TOOLS.has(input.tool)) {
		const output = formatSize(input.output);
		if (output) extra = `Output ${output}`;
	} else if (input.areaTool) {
		const marked = formatSize(input.marked);
		if (marked) extra = `Marked ${marked}`;
	}
	return { size, extra };
}

export function statusText(input: StatusInput): string {
	const { size, extra } = statusParts(input);
	return [size, extra].filter(Boolean).join(' · ');
}
