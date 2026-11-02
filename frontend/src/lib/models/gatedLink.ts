export interface GatedSource {
	license_url?: string | null;
	provider?: string | null;
	ref?: string | null;
	source?: string | null;
	repo_id?: string | null;
	gated_repo?: string | null;
}

const HUB_PROVIDER = 'huggingface';
const HUB_ORIGIN = 'https://huggingface.co';
const REPO_ID = /^[A-Za-z0-9][\w.-]*\/[\w.-]+$/;

function httpUrl(value: string | null | undefined): string | null {
	if (typeof value !== 'string') return null;
	const trimmed = value.trim();
	try {
		const parsed = new URL(trimmed);
		return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? trimmed : null;
	} catch {
		return null;
	}
}

function repoFromRef(ref: string | null | undefined): string | null {
	if (typeof ref !== 'string' || !ref.trim()) return null;
	let candidate: unknown = ref.trim();
	try {
		const parsed = JSON.parse(ref);
		if (parsed && typeof parsed === 'object') {
			const record = parsed as Record<string, unknown>;
			candidate = record.repo ?? record.provider_model_id ?? record.model_id ?? null;
		}
	} catch {
		candidate = ref.trim();
	}
	return typeof candidate === 'string' && REPO_ID.test(candidate) ? candidate : null;
}

function validRepo(value: string | null | undefined): string | null {
	return typeof value === 'string' && REPO_ID.test(value.trim()) ? value.trim() : null;
}

export function gatedPageUrl(source: GatedSource | null | undefined): string | null {
	if (!source) return null;
	const direct = httpUrl(source.license_url);
	if (direct) return direct;
	const explicit = validRepo(source.gated_repo);
	if (explicit) return `${HUB_ORIGIN}/${explicit}`;
	const hub = source.provider === HUB_PROVIDER || source.source === HUB_PROVIDER;
	if (!hub) return null;
	const repo = validRepo(source.repo_id) ?? repoFromRef(source.ref);
	return repo ? `${HUB_ORIGIN}/${repo}` : null;
}
