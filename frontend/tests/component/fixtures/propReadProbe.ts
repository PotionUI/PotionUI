export const propReadProbe = {
	watched: [] as string[],
	runs: {} as Record<string, number>,
	watch(keys: string[]) {
		this.watched = keys;
		this.runs = {};
	},
	record(key: string) {
		this.runs[key] = (this.runs[key] ?? 0) + 1;
	},
	reset() {
		this.runs = {};
	}
};
