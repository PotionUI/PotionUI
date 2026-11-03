const BYTE_UNITS = ['KB', 'MB', 'GB', 'TB', 'PB'];

function oneDecimal(value) {
	const text = value.toFixed(1);
	return text.endsWith('.0') ? text.slice(0, -2) : text;
}

export function formatBytes(bytes) {
	if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes < 0) return '';
	if (bytes < 1024) return `${Math.round(bytes)} B`;
	let value = bytes;
	let unit = -1;
	while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
		value /= 1024;
		unit += 1;
	}
	if (Number(value.toFixed(1)) >= 1024 && unit < BYTE_UNITS.length - 1) {
		value /= 1024;
		unit += 1;
	}
	return `${value.toFixed(1)} ${BYTE_UNITS[unit]}`;
}

export function formatCount(count) {
	if (typeof count !== 'number' || !Number.isFinite(count) || count < 0) return '';
	const n = Math.round(count);
	if (n < 1000) return String(n);
	if (n < 10_000) return `${oneDecimal(n / 1000)}k`;
	if (n < 999_500) return `${Math.round(n / 1000)}k`;
	if (n < 10_000_000) return `${oneDecimal(n / 1_000_000)}M`;
	if (n < 999_500_000) return `${Math.round(n / 1_000_000)}M`;
	return `${oneDecimal(n / 1_000_000_000)}B`;
}

export function formatSpeed(bytesPerSecond) {
	if (typeof bytesPerSecond !== 'number' || !Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return '';
	return `${formatBytes(bytesPerSecond)}/s`;
}

export function percent(fraction) {
	if (typeof fraction !== 'number' || !Number.isFinite(fraction)) return null;
	return Math.max(0, Math.min(100, Math.floor(fraction * 100)));
}

export function formatDate(value) {
	if (value === null || value === undefined || value === '') return '';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return '';
	return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function plural(count, word, many = `${word}s`) {
	return `${count} ${count === 1 ? word : many}`;
}

function hash(seed) {
	let h = 5381;
	for (let i = 0; i < seed.length; i += 1) h = (h * 33) ^ seed.charCodeAt(i);
	return h >>> 0;
}

const VIZ_SLOTS = 8;
const ANGLES = [135, 45, -45, -135];

export function placeholderStyle(seed) {
	const h = hash(String(seed ?? ''));
	const slot = (h % VIZ_SLOTS) + 1;
	const angle = ANGLES[Math.floor(h / VIZ_SLOTS) % ANGLES.length];
	return `background: linear-gradient(${angle}deg, rgb(var(--viz-${slot}) / 0.12), transparent 65%), rgb(var(--surface-3));`;
}

const CIVITAI_IMAGE_HOST = 'image.civitai.com';
const CIVITAI_OPTIONS = /^[a-z]+=[\w.]+(?:,[a-z]+=[\w.]+)*$/i;

export function thumbUrl(url, width, kind = 'image') {
	if (typeof url !== 'string' || !url) return null;
	if (kind === 'video' || !(width > 0)) return url;
	let parsed;
	try {
		parsed = new URL(url);
	} catch {
		return url;
	}
	if (parsed.protocol !== 'https:' || parsed.hostname !== CIVITAI_IMAGE_HOST) return url;
	const parts = parsed.pathname.split('/');
	if (parts.length < 5) return url;
	const index = parts.length - 2;
	if (!CIVITAI_OPTIONS.test(parts[index])) return url;
	parts[index] = `width=${Math.round(width)}`;
	parsed.pathname = parts.join('/');
	return parsed.toString();
}

export function capitalize(text) {
	if (typeof text !== 'string' || !text) return '';
	return text.charAt(0).toUpperCase() + text.slice(1);
}

export function modelTypeLabel(type) {
	if (typeof type !== 'string' || !type) return '';
	const served = globalThis.__potionui?.modelTypes?.get?.(type);
	if (served?.label) return served.label;
	return capitalize(type.replace(/_/g, ' '));
}
