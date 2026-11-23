export interface LatestRequest {
	begin(): number;
	isCurrent(ticket: number): boolean;
	invalidate(): void;
}

export function createLatestRequest(): LatestRequest {
	let latest = 0;
	return {
		begin: () => ++latest,
		isCurrent: (ticket) => ticket === latest,
		invalidate: () => {
			latest++;
		}
	};
}
