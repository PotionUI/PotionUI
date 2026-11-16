import type { EditLink } from './editLink';

export const SAVE_LINK_WAIT_MS = 2500;

export async function editLinkForSave(
	settle: () => Promise<EditLink | null>,
	waitMs: number = SAVE_LINK_WAIT_MS
): Promise<EditLink | null> {
	let timer: ReturnType<typeof setTimeout> | null = null;
	const bound = new Promise<null>((resolve) => {
		timer = setTimeout(() => resolve(null), waitMs);
	});
	try {
		return await Promise.race([settle().catch(() => null), bound]);
	} finally {
		if (timer) clearTimeout(timer);
	}
}
