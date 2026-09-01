/**
 * Client-side "what is this URL" guess for the Add Download modal's detected
 * strip: a filename basename and a best-effort provider match against the
 * already-loaded providers list, keyed off the hostname. No network calls -
 * the server remains the source of truth for what a URL actually resolves to.
 */

export interface DetectedProviderOption {
	id: string;
	name: string;
}

export interface UrlDetection {
	hostname: string | null;
	filename: string | null;
	provider: DetectedProviderOption | null;
}

const EMPTY_DETECTION: UrlDetection = { hostname: null, filename: null, provider: null };

/** Known download-host -> provider-name-fragment hints, for hosts whose
 * provider plugin name doesn't literally contain the hostname's first label
 * (e.g. "huggingface.co" -> a provider named "HuggingFace"). */
const HOST_PROVIDER_HINTS: Record<string, string[]> = {
	'civitai.com': ['civitai'],
	'huggingface.co': ['huggingface', 'hf']
};

function guessFilename(url: URL): string | null {
	const segments = url.pathname.split('/').filter(Boolean);
	const last = segments[segments.length - 1];
	if (!last) return null;
	try {
		return decodeURIComponent(last);
	} catch {
		return last;
	}
}

function matchProvider(
	hostname: string,
	providers: DetectedProviderOption[]
): DetectedProviderOption | null {
	if (providers.length === 0) return null;
	const bareHost = hostname.replace(/^www\./, '');
	const hints =
		Object.entries(HOST_PROVIDER_HINTS).find(
			([host]) => bareHost === host || bareHost.endsWith(`.${host}`)
		)?.[1] ?? [bareHost.split('.')[0]];

	return (
		providers.find((p) => {
			const name = p.name.toLowerCase();
			return hints.some((hint) => name.includes(hint));
		}) ?? null
	);
}

export function detectUrl(rawUrl: string, providers: DetectedProviderOption[] = []): UrlDetection {
	const trimmed = rawUrl.trim();
	if (!trimmed) return EMPTY_DETECTION;

	let parsed: URL;
	try {
		parsed = new URL(trimmed);
	} catch {
		return EMPTY_DETECTION;
	}
	if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return EMPTY_DETECTION;

	const hostname = parsed.hostname.replace(/^www\./, '');
	return {
		hostname,
		filename: guessFilename(parsed),
		provider: matchProvider(hostname, providers)
	};
}
