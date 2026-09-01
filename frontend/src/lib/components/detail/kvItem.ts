export function kvItemValueClass(mono: boolean): string {
	return mono ? 'text-sm text-fg font-mono tabular-nums' : 'text-sm text-fg';
}

export function kvItemWrapperClass(full: boolean): string {
	return full ? 'sm:col-span-2' : '';
}
