import { allowedTypes, can, isUnrestricted, modelTypes, normalizeMe } from './capabilities.js';
import { callPlugin, getMe } from './client.js';
import { baseName, downloadWarnings, sizeProblem, suggestedModelType } from './downloadForm.js';

export { suggestedModelType };
import { formatBytes, formatDate, modelTypeLabel } from './format.js';

export const POLL_INTERVAL_MS = 15_000;

export const LIST_LIMIT = 100;

export const MAX_NOTE_LENGTH = 1000;

export const STATUSES = ['open', 'approved', 'done', 'failed', 'rejected', 'cancelled'];

const STATUS_INFO = {
	open: { label: 'Open', tone: 'info', hint: 'Waiting for an admin to decide' },
	approved: { label: 'Approved', tone: 'signal', hint: 'Approved: the file is being downloaded' },
	done: { label: 'Done', tone: 'success', hint: 'Downloaded and given to the requester' },
	failed: { label: 'Failed', tone: 'danger', hint: 'Approved, but the download failed' },
	rejected: { label: 'Rejected', tone: 'warning', hint: 'An admin turned the request down' },
	cancelled: { label: 'Cancelled', tone: 'neutral', hint: 'Withdrawn by the requester' }
};

const NAIVE_TIME = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/;

function isObject(value) {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function text(value) {
	return typeof value === 'string' ? value : '';
}

function idText(value) {
	return value === undefined || value === null ? '' : String(value);
}

function countOf(value) {
	return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null;
}

function urlWith(value, protocols) {
	if (typeof value !== 'string' || !value) return '';
	try {
		return protocols.includes(new URL(value).protocol) ? value : '';
	} catch {
		return '';
	}
}

function licenseText(value) {
	if (typeof value === 'string') return value;
	return isObject(value) ? text(value.summary) : '';
}

function statusOf(value) {
	return typeof value === 'string' ? value : value?.status;
}

export function statusInfo(status) {
	return STATUS_INFO[status] ?? { label: typeof status === 'string' && status ? status : 'Unknown', tone: 'neutral', hint: '' };
}

export function statusLabel(status) {
	return statusInfo(status).label;
}

export function isOpen(request) {
	return statusOf(request) === 'open';
}

export function normalizeRequest(raw) {
	const source = isObject(raw) ? raw : {};
	const snapshot = isObject(source.snapshot) ? source.snapshot : {};
	const size = snapshot.size_bytes;
	return {
		id: idText(source.id),
		user_id: idText(source.user_id),
		username: text(source.username),
		source: text(source.source),
		source_model_id: idText(source.source_model_id),
		source_version_id: idText(source.source_version_id),
		file_id: idText(source.file_id),
		model_type: text(source.model_type),
		snapshot: {
			name: text(snapshot.name),
			type: text(snapshot.type),
			mb_type: text(snapshot.mb_type),
			version_name: text(snapshot.version_name),
			base_model: text(snapshot.base_model),
			file_name: text(snapshot.file_name),
			size_bytes: typeof size === 'number' && Number.isFinite(size) && size >= 0 ? size : null,
			nsfw: snapshot.nsfw === true,
			poi: snapshot.poi === true,
			license: licenseText(snapshot.license),
			thumbnail_url: urlWith(snapshot.thumbnail_url, ['https:']),
			source_url: urlWith(snapshot.source_url, ['https:', 'http:']),
			sha256: text(snapshot.sha256),
			already_installed: snapshot.already_installed === true,
			hidden: snapshot.hidden === true
		},
		note: text(source.note),
		status: text(source.status),
		decided_by: idText(source.decided_by),
		decided_by_username: text(source.decided_by_username),
		decision_note: text(source.decision_note),
		sha256: text(source.sha256),
		created_at: text(source.created_at),
		updated_at: text(source.updated_at)
	};
}

export function normalizeListing(data) {
	const source = isObject(data) ? data : {};
	const list = Array.isArray(data) ? data : Array.isArray(source.requests) ? source.requests : [];
	return {
		requests: list.filter(isObject).map(normalizeRequest).filter((request) => request.id),
		openCount: countOf(source.open_count),
		canApprove: source.can_approve === true
	};
}

export function parseTime(value) {
	if (typeof value !== 'string' || !value) return null;
	const ms = Date.parse(NAIVE_TIME.test(value) ? `${value.replace(' ', 'T')}Z` : value);
	return Number.isNaN(ms) ? null : ms;
}

export function sortRequests(requests) {
	const time = (request) => parseTime(request.created_at) ?? parseTime(request.updated_at) ?? 0;
	return [...requests].sort((a, b) => {
		const open = Number(isOpen(b)) - Number(isOpen(a));
		if (open) return open;
		const newer = time(b) - time(a);
		if (newer) return newer;
		return a.id < b.id ? 1 : a.id > b.id ? -1 : 0;
	});
}

export function canDecide(me) {
	return can(me, 'approve');
}

export function canDecideOn(me, request) {
	if (!canDecide(me) || !isOpen(request)) return false;
	if (isUnrestricted(me)) return true;
	const userId = me?.user?.id;
	return !userId || request?.user_id !== userId;
}

export function canCancel(me, request) {
	const userId = me?.user?.id;
	return !!userId && isOpen(request) && request?.user_id === userId;
}

export function showsRequests(me) {
	return can(me, 'request') || can(me, 'approve');
}

export function groupRequests(requests, me) {
	const sorted = sortRequests(requests);
	const userId = me?.user?.id ?? '';
	return {
		mine: userId ? sorted.filter((request) => request.user_id === userId) : sorted,
		review: canDecide(me) ? sorted.filter(isOpen) : [],
		all: sorted
	};
}

export function panelTabs(me, reviewCount = null) {
	if (!canDecide(me)) return [{ id: 'mine', label: 'My requests' }];
	const review = { id: 'review', label: 'To review' };
	if (typeof reviewCount === 'number') review.count = reviewCount;
	const tabs = [review];
	if (can(me, 'request') && !isUnrestricted(me)) tabs.push({ id: 'mine', label: 'Mine' });
	tabs.push({ id: 'all', label: 'All' });
	return tabs;
}

export function defaultTab(me) {
	return canDecide(me) ? 'review' : 'mine';
}

export function tabQuery(tab, status = '') {
	if (tab === 'review') return { status: 'open', all: true, limit: LIST_LIMIT };
	if (tab === 'all') return STATUSES.includes(status) ? { status, all: true, limit: LIST_LIMIT } : { all: true, limit: LIST_LIMIT };
	return { all: false, limit: LIST_LIMIT };
}

export function tabRequests(tab, requests, me, status = '') {
	const groups = groupRequests(requests, me);
	if (tab === 'review') return groups.review;
	if (tab === 'mine') return groups.mine;
	return STATUSES.includes(status) ? groups.all.filter((request) => request.status === status) : groups.all;
}

export function badgeCount(me, listing) {
	const requests = listing?.requests ?? [];
	if (canDecide(me)) return typeof listing?.openCount === 'number' ? listing.openCount : requests.filter(isOpen).length;
	const userId = me?.user?.id ?? '';
	return requests.filter((request) => isOpen(request) && (!userId || request.user_id === userId)).length;
}

export function initialBadgeCount(me) {
	const count = me?.requests?.open_count;
	return canDecide(me) && typeof count === 'number' ? count : null;
}

export async function fetchBadgeCount(api, me) {
	if (canDecide(me)) {
		const meResult = await getMe(api);
		const fromMe = meResult.ok ? normalizeMe(meResult.data).requests.open_count : null;
		if (fromMe !== null) return fromMe;
		const listed = await listRequests(api, tabQuery('review'));
		return listed.ok ? badgeCount(me, normalizeListing(listed.data)) : null;
	}
	const listed = await listRequests(api, { status: 'open', all: false, limit: LIST_LIMIT });
	return listed.ok ? badgeCount(me, normalizeListing(listed.data)) : null;
}

function requestPath(id, action = '') {
	return `/requests/${encodeURIComponent(String(id))}${action ? `/${action}` : ''}`;
}

function cleanNote(note) {
	return typeof note === 'string' ? note.trim() : '';
}

export function createRequest(api, body) {
	return callPlugin(api, 'post', '/requests', body);
}

export function listRequests(api, { status, all, limit } = {}) {
	const params = new URLSearchParams();
	if (typeof status === 'string' && status) params.set('status', status);
	if (typeof all === 'boolean') params.set('all', String(all));
	if (typeof limit === 'number') params.set('limit', String(limit));
	const query = params.toString();
	return callPlugin(api, 'get', `/requests${query ? `?${query}` : ''}`);
}

export function approveRequest(api, id, { note, modelType, assignGroupId } = {}) {
	const body = {};
	const cleaned = cleanNote(note);
	if (cleaned) body.note = cleaned;
	if (typeof modelType === 'string' && modelType) body.model_type = modelType;
	if (typeof assignGroupId === 'string' && assignGroupId) body.assign_group_id = assignGroupId;
	return callPlugin(api, 'post', requestPath(id, 'approve'), body);
}

export function rejectRequest(api, id, { note } = {}) {
	const cleaned = cleanNote(note);
	return callPlugin(api, 'post', requestPath(id, 'reject'), cleaned ? { note: cleaned } : {});
}

export function cancelRequest(api, id) {
	return callPlugin(api, 'delete', requestPath(id));
}

export function initialRequestForm(me, detail, file = null) {
	return { modelType: allowedTypes(me, suggestedModelType(detail, file), { purpose: 'request' }).selected, note: '' };
}

export function requestKey(source, modelId, versionId, fileId) {
	return [source, modelId, versionId, fileId].map(idText).join('\u0000');
}

export async function fetchPendingKeys(api) {
	const result = await callPlugin(api, 'get', '/requests/pending');
	if (!result.ok) return null;
	const keys = new Set();
	for (const entry of Array.isArray(result.data?.keys) ? result.data.keys : []) {
		if (isObject(entry)) keys.add(requestKey(entry.source, entry.model_id, entry.version_id, entry.file_id));
	}
	return keys;
}

export function withPending(keys, request) {
	const normalized = normalizeRequest(request);
	if (!normalized.id || !isOpen(normalized)) return keys;
	const next = new Set(keys);
	next.add(requestKey(normalized.source, normalized.source_model_id, normalized.source_version_id, normalized.file_id));
	return next;
}

function noteProblem(note) {
	return cleanNote(note).length > MAX_NOTE_LENGTH ? `At most ${MAX_NOTE_LENGTH} characters` : '';
}

export function requestFormProblems(form, { me, detail, file }) {
	const { types } = allowedTypes(me, detail?.mb_type, { purpose: 'request' });
	let modelType = '';
	if (!form.modelType) modelType = 'Choose what to file this model as';
	else if (!types.includes(form.modelType)) modelType = 'Your permissions do not allow requesting this model type';
	return { modelType, note: noteProblem(form.note), size: sizeProblem(me, file?.size_bytes, { purpose: 'request' }) };
}

export function buildRequestBody({ detail, version, file, form }) {
	const body = {
		source: detail.source,
		model_id: String(detail.id),
		version_id: String(version.id),
		file_id: String(file.id)
	};
	if (form.modelType) body.model_type = form.modelType;
	const note = cleanNote(form.note);
	if (note) body.note = note;
	return body;
}

export function requestWarnings({ detail, version, file }) {
	const warnings = [];
	if (detail?.nsfw || version?.nsfw) {
		warnings.push({
			tone: 'warning',
			title: 'NSFW',
			text: 'This model is marked NSFW: the admin sees it in your request, and the approval checks your content policy again.'
		});
	}
	return [...warnings, ...downloadWarnings({ detail, version, file })];
}

export function requestedType(request) {
	return request?.model_type || request?.snapshot?.mb_type || '';
}

export function requestTitle(request) {
	return request?.snapshot?.name || request?.snapshot?.file_name || 'Model';
}

export function requestMeta(request, sourceLabel) {
	const snapshot = request?.snapshot ?? {};
	const type = modelTypeLabel(requestedType(request)) || snapshot.type;
	const source = request?.source ? (sourceLabel ? sourceLabel(request.source) : request.source) : '';
	return [snapshot.version_name, snapshot.file_name, formatBytes(snapshot.size_bytes), type, source].filter(Boolean);
}

export function sourceHost(request) {
	try {
		return new URL(request?.snapshot?.source_url ?? '').hostname;
	} catch {
		return '';
	}
}

export function requestByline(request, { showUser = true } = {}) {
	const time = parseTime(request?.created_at);
	const date = time === null ? '' : formatDate(time);
	const title = time === null ? '' : new Date(time).toLocaleString();
	if (!showUser) return { text: date ? `Asked on ${date}` : '', title };
	return { text: [`Asked by ${request?.username || 'an unknown user'}`, date].filter(Boolean).join(' · '), title };
}

export function decisionLine(request) {
	const by = request?.decided_by_username;
	switch (request?.status) {
		case 'approved':
		case 'done':
		case 'failed':
			return by ? `Approved by ${by}` : 'Approved';
		case 'rejected':
			return by ? `Rejected by ${by}` : 'Rejected';
		default:
			return '';
	}
}

export function approvalTypeChoice(request) {
	const requested = requestedType(request);
	const known = modelTypes();
	return {
		types: requested ? [requested, ...known.filter((type) => type !== requested)] : known,
		selected: requested
	};
}

export function approvalProblems(request, form) {
	return {
		modelType: !form?.modelType && !requestedType(request) ? 'Choose what to file this model as' : '',
		note: noteProblem(form?.note)
	};
}

export function approvalOptions(request, form) {
	const modelType = typeof form?.modelType === 'string' ? form.modelType : '';
	return { note: cleanNote(form?.note), modelType: modelType && modelType !== requestedType(request) ? modelType : '' };
}

function fileOf(request) {
	return request?.snapshot?.file_name || request?.snapshot?.name || 'the file';
}

function whoOf(request) {
	return request?.username || 'the requester';
}

function decisionError(error, verb) {
	if (error?.code === 'not_open') return { level: 'info', message: 'This request was already handled', refresh: true };
	if (error?.code === 'not_found') return { level: 'info', message: 'This request no longer exists', refresh: true };
	return { level: 'error', message: `Could not ${verb} the request: ${error?.message || 'unknown error'}`, refresh: false };
}

export function createToast(result, file) {
	const name = baseName(file?.name) || 'This file';
	if (result.ok) {
		if (result.data?.already_requested) {
			return { level: 'info', message: `You already asked for ${name}: your request is still open`, close: true };
		}
		return { level: 'success', message: `Request sent: an admin will review ${name}`, close: true };
	}
	if (result.error?.code === 'already_available') return { level: 'info', message: `${name} is already in your models`, close: true };
	return { level: 'error', message: `Request refused: ${result.error?.message || 'unknown error'}`, close: false };
}

export function approveToast(result, request) {
	if (!result.ok) return decisionError(result.error, 'approve');
	const data = result.data ?? {};
	if (data.already_installed) {
		return { level: 'success', message: `Approved: ${fileOf(request)} was already installed, now assigned to ${whoOf(request)}`, refresh: true };
	}
	if (data.already_queued) return { level: 'info', message: `Approved: ${fileOf(request)} is already being downloaded`, refresh: true };
	return { level: 'success', message: `Approved: downloading ${fileOf(request)} for ${whoOf(request)}`, refresh: true };
}

export function rejectToast(result, request) {
	if (!result.ok) return decisionError(result.error, 'reject');
	return { level: 'success', message: `Rejected the request of ${whoOf(request)} for ${fileOf(request)}`, refresh: true };
}

export function cancelToast(result, request) {
	if (!result.ok) return decisionError(result.error, 'cancel');
	return { level: 'success', message: `Request cancelled: ${fileOf(request)}`, refresh: true };
}
