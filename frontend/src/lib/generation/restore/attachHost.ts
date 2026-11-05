export type AttachHost = (tabId: string, generationIds: string[]) => Promise<void>;

let host: AttachHost | null = null;

export function setGenerationAttachHost(next: AttachHost | null): void {
	host = next;
}

export async function attachExistingGenerations(tabId: string, generationIds: string[]): Promise<boolean> {
	if (!host) return false;
	if (generationIds.length === 0) return true;
	await host(tabId, generationIds);
	return true;
}
