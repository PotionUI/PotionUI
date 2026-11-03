import { modelTypeLabel } from './format.js';

const CAPABILITIES = ['browse', 'request', 'download', 'approve'];

export function modelTypes() {
	const definitions = globalThis.__potionui?.modelTypes?.list?.() ?? [];
	return definitions.filter((definition) => !definition.virtual).map((definition) => definition.id);
}

const GIB = 1024 ** 3;
const NSFW_CEILINGS = ['allowed', 'blur', 'blocked'];
const NSFW_MODES = ['blur', 'show', 'hide'];

function isObject(value) {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function strings(value) {
	return Array.isArray(value) ? [...new Set(value.filter((item) => typeof item === 'string' && item))] : [];
}

function normalizeParams(value) {
	const params = isObject(value) ? value : {};
	const gb = params.max_file_gb;
	return {
		sources: strings(params.sources),
		model_types: strings(params.model_types),
		max_file_gb: typeof gb === 'number' && Number.isFinite(gb) && gb > 0 ? gb : null,
		assign_to: params.assign_to === 'group' ? 'group' : 'user',
		unrestricted: params.unrestricted === true
	};
}

export function normalizeMe(data) {
	const source = isObject(data) ? data : {};
	const user = isObject(source.user) ? source.user : {};
	const nsfw = isObject(source.nsfw) ? source.nsfw : {};
	const requests = isObject(source.requests) ? source.requests : {};
	const isAdmin = user.is_admin === true;
	const params = normalizeParams(source.params);
	const requestParams = isObject(source.request_params) ? normalizeParams(source.request_params) : { ...params };

	const ceiling = NSFW_CEILINGS.includes(nsfw.ceiling) ? nsfw.ceiling : 'blocked';
	let allowedModes = strings(nsfw.allowed_modes).filter((mode) => NSFW_MODES.includes(mode));
	if (ceiling === 'blocked' || allowedModes.length === 0) allowedModes = ['hide'];
	const mode = NSFW_MODES.includes(nsfw.mode) && allowedModes.includes(nsfw.mode) ? nsfw.mode : allowedModes[0];

	return {
		user: {
			id: user.id === undefined || user.id === null ? '' : String(user.id),
			username: typeof user.username === 'string' ? user.username : '',
			is_admin: isAdmin
		},
		capabilities: strings(source.capabilities).filter((capability) => CAPABILITIES.includes(capability)),
		params: { ...params, allow_remote: isObject(source.params) && source.params.allow_remote === true },
		request_params: { ...requestParams, unrestricted: requestParams.unrestricted || params.unrestricted },
		nsfw: {
			ceiling,
			mode,
			allowed_modes: allowedModes,
			max_level: typeof nsfw.max_level === 'number' ? nsfw.max_level : null,
			hide_poi: nsfw.hide_poi === true,
			setting_key: typeof nsfw.setting_key === 'string' && nsfw.setting_key ? nsfw.setting_key : 'media_nsfw_filter_mode'
		},
		groups: (Array.isArray(source.groups) ? source.groups : [])
			.filter((group) => isObject(group) && group.id !== undefined && group.id !== null && group.id !== '')
			.map((group) => ({ id: String(group.id), name: typeof group.name === 'string' && group.name ? group.name : String(group.id) })),
		requests: {
			open_count:
				typeof requests.open_count === 'number' && Number.isFinite(requests.open_count) && requests.open_count >= 0
					? Math.floor(requests.open_count)
					: null
		}
	};
}

export function isUnrestricted(me) {
	return me?.params?.unrestricted === true || me?.user?.is_admin === true;
}

export function can(me, capability) {
	if (!me) return false;
	if (isUnrestricted(me)) return true;
	return Array.isArray(me.capabilities) && me.capabilities.includes(capability);
}

export function paramsFor(me, purpose = 'download') {
	if (purpose === 'request') return me?.request_params ?? me?.params ?? null;
	return me?.params ?? null;
}

export function allowedTypes(me, mbType, { purpose = 'download' } = {}) {
	const listed = paramsFor(me, purpose)?.model_types ?? [];
	const all = isUnrestricted(me) || listed.length === 0;
	const known = modelTypes();
	let types = all ? [...known] : known.filter((type) => listed.includes(type));
	if (!all) types = [...types, ...listed.filter((type) => !known.includes(type))];
	const suggestion = typeof mbType === 'string' && mbType ? mbType : '';
	const suggestionAllowed = suggestion !== '' && types.includes(suggestion);
	if (suggestionAllowed) types = [suggestion, ...types.filter((type) => type !== suggestion)];
	return { types, selected: suggestionAllowed ? suggestion : '', suggestionAllowed };
}

export function canChooseGroup(me) {
	return isUnrestricted(me) || me?.params?.assign_to === 'group';
}

export function canChooseRemote(me) {
	return isUnrestricted(me) || me?.params?.allow_remote === true;
}

export function canDownloadFrom(me, source) {
	if (!can(me, 'download')) return false;
	return isUnrestricted(me) || (me?.params?.sources ?? []).includes(source);
}

export function canRequestFrom(me, source) {
	if (!can(me, 'request')) return false;
	return isUnrestricted(me) || (paramsFor(me, 'request')?.sources ?? []).includes(source);
}

export function requestBlock(me, { source = '', type = null, sizeBytes = null } = {}) {
	if (!can(me, 'request')) return 'capability';
	if (isUnrestricted(me)) return '';
	if (!canRequestFrom(me, source)) return 'source';
	const types = paramsFor(me, 'request')?.model_types ?? [];
	if (typeof type === 'string' && type && types.length > 0 && !types.includes(type)) return 'type';
	if (exceedsSizeLimit(me, sizeBytes, { purpose: 'request' })) return 'size';
	return '';
}

export function requestBlockReason(me, file = {}, sourceLabel = '') {
	switch (requestBlock(me, file)) {
		case 'capability':
			return 'You may not ask for models';
		case 'source':
			return `Not allowed from ${sourceLabel || file.source || 'this source'}`;
		case 'type':
			return `${modelTypeLabel(file.type) || file.type} not allowed`;
		case 'size':
			return `Over your ${paramsFor(me, 'request')?.max_file_gb} GB limit`;
		default:
			return '';
	}
}

export function fileAction(me, source, { type = null, sizeBytes = null } = {}) {
	const askable = can(me, 'request') && !requestBlock(me, { source, type, sizeBytes });
	if (can(me, 'download')) {
		if (canDownloadFrom(me, source)) return 'download';
		return askable ? 'request' : 'source-blocked';
	}
	if (can(me, 'request')) return askable ? 'request' : 'request-blocked';
	return 'none';
}

export function fileActionInfo(me, source, file = {}, sourceLabel = '') {
	const action = fileAction(me, source, file);
	if (action === 'source-blocked') return { action, reason: `Not allowed from ${sourceLabel || source || 'this source'}` };
	if (action === 'request-blocked') return { action, reason: requestBlockReason(me, { ...file, source }, sourceLabel) };
	return { action, reason: '' };
}

export function sizeLimitBytes(me, { purpose = 'download' } = {}) {
	if (isUnrestricted(me)) return null;
	const gb = paramsFor(me, purpose)?.max_file_gb;
	return typeof gb === 'number' && gb > 0 ? gb * GIB : null;
}

export function exceedsSizeLimit(me, sizeBytes, { purpose = 'download' } = {}) {
	const limit = sizeLimitBytes(me, { purpose });
	return limit !== null && typeof sizeBytes === 'number' && sizeBytes > limit;
}

export function groupChoices(me) {
	return [{ id: '', name: 'Me' }, ...(me?.groups ?? [])];
}
