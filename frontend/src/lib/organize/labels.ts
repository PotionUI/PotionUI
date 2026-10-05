import { api } from '$lib/services/api';
import { modelDisplayName } from '$lib/utils/modelDisplay';

const modelNames = new Map<string, string>();
const pending = new Map<string, Promise<void>>();

export function rememberModelName(id: string, name: string): void {
	if (id && name) modelNames.set(id, name);
}

export function cachedModelName(id: string): string | undefined {
	return modelNames.get(id);
}

export function resetModelNames(): void {
	modelNames.clear();
	pending.clear();
}

async function loadOne(id: string): Promise<void> {
	try {
		const response = await api.getModelById(id);
		const name = modelDisplayName(response.data?.model);
		if (response.success && name) modelNames.set(id, name);
	} catch {
		return;
	}
}

export async function resolveModelNames(ids: string[]): Promise<Record<string, string>> {
	const wanted = [...new Set(ids.filter(Boolean))];
	await Promise.all(
		wanted
			.filter((id) => !modelNames.has(id))
			.map((id) => {
				let job = pending.get(id);
				if (!job) {
					job = loadOne(id).finally(() => pending.delete(id));
					pending.set(id, job);
				}
				return job;
			})
	);
	const out: Record<string, string> = {};
	for (const id of wanted) {
		const name = modelNames.get(id);
		if (name) out[id] = name;
	}
	return out;
}
