export const control: { resolve: (() => void) | null } = { resolve: null };

export function run(): Promise<void> {
	return new Promise((resolve) => {
		control.resolve = resolve;
	});
}
