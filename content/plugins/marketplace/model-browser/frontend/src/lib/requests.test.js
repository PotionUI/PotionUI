import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeMe } from './capabilities.js';
import { SERVED_MODEL_TYPES, installHostModelTypes } from './testModelTypes.js';
import { formatDate } from './format.js';
import {
	LIST_LIMIT,
	MAX_NOTE_LENGTH,
	STATUSES,
	approvalOptions,
	approvalProblems,
	approvalTypeChoice,
	approveRequest,
	approveToast,
	badgeCount,
	buildRequestBody,
	canCancel,
	canDecide,
	canDecideOn,
	cancelRequest,
	cancelToast,
	createRequest,
	createToast,
	decisionLine,
	defaultTab,
	fetchBadgeCount,
	fetchPendingKeys,
	groupRequests,
	initialBadgeCount,
	initialRequestForm,
	isOpen,
	listRequests,
	normalizeListing,
	normalizeRequest,
	panelTabs,
	parseTime,
	rejectRequest,
	rejectToast,
	requestByline,
	requestFormProblems,
	requestMeta,
	requestTitle,
	requestKey,
	requestWarnings,
	requestedType,
	showsRequests,
	sortRequests,
	sourceHost,
	suggestedModelType,
	statusInfo,
	statusLabel,
	tabQuery,
	tabRequests,
	withPending
} from './requests.js';

function meData(overrides = {}) {
	return {
		user: { id: 'u1', username: 'alice', is_admin: false },
		capabilities: ['browse', 'request'],
		params: { sources: [], model_types: [], max_file_gb: null, assign_to: 'user', allow_remote: false, unrestricted: false },
		nsfw: { ceiling: 'allowed', mode: 'blur', allowed_modes: ['blur', 'show', 'hide'], max_level: 2, hide_poi: false },
		groups: [],
		requests: { open_count: 0 },
		...overrides
	};
}

const REQUESTER = normalizeMe(meData());
const LIMITED = normalizeMe(meData({ params: { sources: ['civitai'], model_types: ['embedding', 'lora'], max_file_gb: 1, assign_to: 'user' } }));
const APPROVER = normalizeMe(
	meData({ user: { id: 'u9', username: 'bob', is_admin: false }, capabilities: ['approve', 'browse', 'request'], requests: { open_count: 3 } })
);
const ADMIN = normalizeMe(
	meData({
		user: { id: 'a1', username: 'root', is_admin: true },
		capabilities: ['approve', 'browse', 'download', 'request'],
		params: { unrestricted: true },
		requests: { open_count: 2 }
	})
);
const BROWSER = normalizeMe(meData({ capabilities: ['browse'] }));

function request(id, status, overrides = {}) {
	return {
		id,
		user_id: 'u1',
		username: 'alice',
		source: 'civitai',
		source_model_id: '58390',
		source_version_id: '135867',
		file_id: '89277',
		snapshot: {
			name: 'Detail Tweaker LoRA',
			type: 'LORA',
			mb_type: 'lora',
			version_name: 'v1.0',
			base_model: 'SD 1.5',
			file_name: 'add_detail.safetensors',
			size_bytes: 37_861_176,
			nsfw: false,
			poi: false,
			license: 'Commercial use allowed',
			thumbnail_url: 'https://image.civitai.com/x/y/width=450/1.jpeg',
			source_url: 'https://civitai.com/models/58390',
			sha256: 'a'.repeat(64),
			already_installed: false
		},
		note: 'For the portraits',
		status,
		decided_by: null,
		decided_by_username: null,
		decision_note: null,
		sha256: null,
		created_at: '2026-10-11 18:00:00',
		updated_at: '2026-10-11 18:00:00',
		...overrides
	};
}

function fakeApi(handler) {
	const calls = [];
	const call = (method) => async (path, body) => {
		calls.push(body === undefined ? [method, path] : [method, path, body]);
		return handler(method, path, body);
	};
	return { calls, get: call('get'), post: call('post'), put: call('put'), delete: call('delete') };
}

const ok = (data) => ({ success: true, data });
const fail = (error, message) => ({ detail: { success: false, error, message } });

installHostModelTypes();

test('statusInfo gives every status a label, a tone and a hint', () => {
	for (const status of STATUSES) {
		const info = statusInfo(status);
		assert.ok(info.label && info.tone && info.hint, status);
	}
	assert.deepEqual(statusInfo('open'), { label: 'Open', tone: 'info', hint: 'Waiting for an admin to decide' });
	assert.equal(statusLabel('done'), 'Done');
	assert.equal(statusInfo('failed').tone, 'danger');
	assert.equal(statusInfo('rejected').tone, 'warning');
	assert.deepEqual(statusInfo('paused'), { label: 'paused', tone: 'neutral', hint: '' });
	assert.equal(statusLabel(undefined), 'Unknown');
});

test('isOpen reads a request or a status', () => {
	assert.equal(isOpen('open'), true);
	assert.equal(isOpen(request('r1', 'open')), true);
	assert.equal(isOpen(request('r1', 'approved')), false);
	assert.equal(isOpen(null), false);
});

test('normalizeRequest keeps a well formed request', () => {
	const normal = normalizeRequest(request('r1', 'open', { decision_note: null }));
	assert.equal(normal.id, 'r1');
	assert.equal(normal.status, 'open');
	assert.equal(normal.snapshot.size_bytes, 37_861_176);
	assert.equal(normal.snapshot.version_name, 'v1.0');
	assert.equal(normal.snapshot.base_model, 'SD 1.5');
	assert.equal(normal.snapshot.license, 'Commercial use allowed');
	assert.equal(normal.snapshot.thumbnail_url, 'https://image.civitai.com/x/y/width=450/1.jpeg');
	assert.equal(normal.snapshot.source_url, 'https://civitai.com/models/58390');
	assert.equal(normal.snapshot.poi, false);
	assert.equal(normal.snapshot.hidden, false);
	assert.equal(normal.decided_by_username, '');
	assert.equal(normal.decision_note, '');
	assert.equal(normal.model_type, '');
});

test('normalizeRequest fills in what is missing or malformed', () => {
	const normal = normalizeRequest({
		id: 7,
		snapshot: { size_bytes: -1, nsfw: 'yes', poi: 1, source_url: 'javascript:alert(1)', thumbnail_url: 'http://x.example/y.jpg', hidden: true }
	});
	assert.equal(normal.id, '7');
	assert.equal(normal.status, '');
	assert.equal(normal.snapshot.size_bytes, null);
	assert.equal(normal.snapshot.nsfw, false);
	assert.equal(normal.snapshot.poi, false);
	assert.equal(normal.snapshot.source_url, '');
	assert.equal(normal.snapshot.thumbnail_url, '');
	assert.equal(normal.snapshot.hidden, true);
	assert.equal(normal.snapshot.license, '');
	assert.equal(normalizeRequest({ id: 'x', snapshot: { license: { summary: 'CreativeML Open RAIL-M' } } }).snapshot.license, 'CreativeML Open RAIL-M');
	assert.equal(normalizeRequest({ id: 'x', snapshot: { source_url: 'http://example.com/m' } }).snapshot.source_url, 'http://example.com/m');
	assert.equal(normalizeRequest(null).id, '');
	assert.equal(normalizeRequest('x').snapshot.name, '');
});

test('normalizeListing reads the envelope data, a bare list, or nothing', () => {
	const listing = normalizeListing({ requests: [request('r1', 'open'), null, { note: 'no id' }], open_count: 4, can_approve: true });
	assert.deepEqual(listing.requests.map((entry) => entry.id), ['r1']);
	assert.equal(listing.openCount, 4);
	assert.equal(listing.canApprove, true);
	assert.deepEqual(normalizeListing([request('r2', 'done')]).requests.map((entry) => entry.id), ['r2']);
	assert.deepEqual(normalizeListing(undefined), { requests: [], openCount: null, canApprove: false });
	assert.equal(normalizeListing({ requests: [], open_count: -2 }).openCount, null);
	assert.equal(normalizeListing({ requests: [], open_count: 2.7 }).openCount, 2);
});

test('parseTime reads ISO text, and server text without a zone as UTC', () => {
	assert.equal(parseTime('2026-10-11 18:00:00'), Date.UTC(2026, 9, 11, 18, 0, 0));
	assert.equal(parseTime('2026-10-11T18:00:00'), Date.UTC(2026, 9, 11, 18, 0, 0));
	assert.equal(parseTime('2026-10-11T18:00:00+02:00'), Date.UTC(2026, 9, 11, 16, 0, 0));
	assert.equal(parseTime('soon'), null);
	assert.equal(parseTime(''), null);
	assert.equal(parseTime(42), null);
});

test('sortRequests puts open requests first, then the newest, without mutating', () => {
	const list = [
		normalizeRequest(request('r1', 'done', { created_at: '2026-10-11 10:00:00' })),
		normalizeRequest(request('r2', 'open', { created_at: '2026-10-10 10:00:00' })),
		normalizeRequest(request('r3', 'rejected', { created_at: '2026-10-11 12:00:00' })),
		normalizeRequest(request('r4', 'open', { created_at: '2026-10-11 09:00:00' })),
		normalizeRequest(request('r6', 'cancelled', { created_at: '', updated_at: '' })),
		normalizeRequest(request('r5', 'cancelled', { created_at: '', updated_at: '' }))
	];
	const before = list.map((entry) => entry.id);
	assert.deepEqual(sortRequests(list).map((entry) => entry.id), ['r4', 'r2', 'r3', 'r1', 'r6', 'r5']);
	assert.deepEqual(list.map((entry) => entry.id), before);
});

test('groupRequests splits own, to review and all', () => {
	const list = normalizeListing({
		requests: [request('r1', 'open'), request('r2', 'open', { user_id: 'u2', username: 'carol' }), request('r3', 'done', { user_id: 'u9' })]
	}).requests;
	const forApprover = groupRequests(list, APPROVER);
	assert.deepEqual(forApprover.mine.map((entry) => entry.id), ['r3']);
	assert.deepEqual(forApprover.review.map((entry) => entry.id).sort(), ['r1', 'r2']);
	assert.equal(forApprover.all.length, 3);
	const forRequester = groupRequests(list, REQUESTER);
	assert.deepEqual(forRequester.mine.map((entry) => entry.id), ['r1']);
	assert.deepEqual(forRequester.review, []);
	assert.equal(groupRequests(list, null).mine.length, 3);
});

test('panelTabs and defaultTab follow the capabilities', () => {
	assert.deepEqual(panelTabs(REQUESTER), [{ id: 'mine', label: 'My requests' }]);
	assert.deepEqual(panelTabs(APPROVER, 3), [
		{ id: 'review', label: 'To review', count: 3 },
		{ id: 'mine', label: 'Mine' },
		{ id: 'all', label: 'All' }
	]);
	assert.deepEqual(panelTabs(ADMIN).map((tab) => tab.id), ['review', 'all']);
	assert.equal('count' in panelTabs(ADMIN)[0], false);
	assert.equal(defaultTab(APPROVER), 'review');
	assert.equal(defaultTab(REQUESTER), 'mine');
});

test('tabQuery asks the server for what a tab shows', () => {
	assert.deepEqual(tabQuery('review'), { status: 'open', all: true, limit: LIST_LIMIT });
	assert.deepEqual(tabQuery('mine'), { all: false, limit: LIST_LIMIT });
	assert.deepEqual(tabQuery('all'), { all: true, limit: LIST_LIMIT });
	assert.deepEqual(tabQuery('all', 'rejected'), { status: 'rejected', all: true, limit: LIST_LIMIT });
	assert.deepEqual(tabQuery('all', 'bogus'), { all: true, limit: LIST_LIMIT });
});

test('tabRequests filters again whatever the server sent', () => {
	const list = normalizeListing({ requests: [request('r1', 'open'), request('r2', 'done', { user_id: 'u9' }), request('r3', 'rejected')] }).requests;
	assert.deepEqual(tabRequests('review', list, APPROVER).map((entry) => entry.id), ['r1']);
	assert.deepEqual(tabRequests('mine', list, APPROVER).map((entry) => entry.id), ['r2']);
	assert.deepEqual(tabRequests('all', list, APPROVER, 'rejected').map((entry) => entry.id), ['r3']);
	assert.equal(tabRequests('all', list, APPROVER).length, 3);
	assert.deepEqual(tabRequests('review', list, REQUESTER), []);
});

test('canDecideOn: approvers decide on open requests of others; admins on their own too', () => {
	const fromAlice = normalizeRequest(request('r1', 'open'));
	const fromBob = normalizeRequest(request('r2', 'open', { user_id: 'u9', username: 'bob' }));
	const fromRoot = normalizeRequest(request('r3', 'open', { user_id: 'a1', username: 'root' }));
	assert.equal(canDecideOn(APPROVER, fromAlice), true);
	assert.equal(canDecideOn(APPROVER, fromBob), false);
	assert.equal(canDecideOn(APPROVER, { ...fromAlice, status: 'approved' }), false);
	assert.equal(canDecideOn(ADMIN, fromRoot), true);
	assert.equal(canDecideOn(ADMIN, fromAlice), true);
	assert.equal(canDecideOn(REQUESTER, fromBob), false);
	assert.equal(canDecideOn(null, fromAlice), false);
});

test('the request form reads the request parameters, not the download ones', () => {
	const me = normalizeMe(
		meData({
			capabilities: ['browse', 'download', 'request'],
			params: { sources: ['civitai'], model_types: ['lora'], max_file_gb: 1 },
			request_params: { sources: ['civitai', 'huggingface'], model_types: ['embedding', 'checkpoint'], max_file_gb: 10 }
		})
	);
	assert.deepEqual(initialRequestForm(me, DETAIL), { modelType: '', note: '' });
	assert.deepEqual(initialRequestForm(me, { ...DETAIL, mb_type: 'checkpoint' }), { modelType: 'checkpoint', note: '' });
	const big = { ...FILE, size_bytes: 5 * 1024 ** 3 };
	assert.deepEqual(requestFormProblems({ modelType: 'checkpoint', note: '' }, { me, detail: DETAIL, file: big }), {
		modelType: '',
		note: '',
		size: ''
	});
	assert.equal(
		requestFormProblems({ modelType: 'lora', note: '' }, { me, detail: DETAIL, file: FILE }).modelType,
		'Your permissions do not allow requesting this model type'
	);
	assert.equal(
		requestFormProblems({ modelType: 'embedding', note: '' }, { me, detail: DETAIL, file: { ...FILE, size_bytes: 11 * 1024 ** 3 } }).size,
		'This file is 11.0 GB: your permissions let you ask for files of at most 10 GB'
	);
});

test('canDecide, canCancel and showsRequests', () => {
	assert.equal(canDecide(APPROVER), true);
	assert.equal(canDecide(ADMIN), true);
	assert.equal(canDecide(REQUESTER), false);
	assert.equal(canDecide(null), false);
	const mine = normalizeRequest(request('r1', 'open'));
	assert.equal(canCancel(REQUESTER, mine), true);
	assert.equal(canCancel(REQUESTER, { ...mine, status: 'approved' }), false);
	assert.equal(canCancel(APPROVER, mine), false);
	assert.equal(canCancel(ADMIN, mine), false);
	assert.equal(canCancel(normalizeMe({}), { ...mine, user_id: '' }), false);
	assert.equal(showsRequests(REQUESTER), true);
	assert.equal(showsRequests(APPROVER), true);
	assert.equal(showsRequests(normalizeMe(meData({ capabilities: ['approve'] }))), true);
	assert.equal(showsRequests(BROWSER), false);
	assert.equal(showsRequests(null), false);
});

test('badgeCount: open_count for approvers, own open requests for the others', () => {
	const listing = normalizeListing({
		requests: [request('r1', 'open'), request('r2', 'open', { user_id: 'u2' }), request('r3', 'done')],
		open_count: 5
	});
	assert.equal(badgeCount(APPROVER, listing), 5);
	assert.equal(badgeCount(APPROVER, { ...listing, openCount: null }), 2);
	assert.equal(badgeCount(REQUESTER, listing), 1);
	assert.equal(badgeCount(REQUESTER, null), 0);
	assert.equal(initialBadgeCount(APPROVER), 3);
	assert.equal(initialBadgeCount(ADMIN), 2);
	assert.equal(initialBadgeCount(REQUESTER), null);
	assert.equal(initialBadgeCount(normalizeMe(meData({ capabilities: ['approve'], requests: {} }))), null);
});

test('fetchBadgeCount reads /me for approvers', async () => {
	const api = fakeApi(() => ok(meData({ capabilities: ['approve', 'browse'], requests: { open_count: 6 } })));
	assert.equal(await fetchBadgeCount(api, APPROVER), 6);
	assert.deepEqual(api.calls, [['get', '/me']]);
});

test('fetchBadgeCount falls back to the open requests when /me has no count', async () => {
	const api = fakeApi((method, path) =>
		path === '/me' ? ok(meData({ requests: undefined })) : ok({ requests: [request('r1', 'open'), request('r2', 'open')] })
	);
	assert.equal(await fetchBadgeCount(api, APPROVER), 2);
	assert.deepEqual(api.calls, [['get', '/me'], ['get', `/requests?status=open&all=true&limit=${LIST_LIMIT}`]]);
});

test('fetchBadgeCount counts a requester own open requests, and says null on failure', async () => {
	const api = fakeApi(() => ok({ requests: [request('r1', 'open'), request('r2', 'cancelled')], open_count: 0, can_approve: false }));
	assert.equal(await fetchBadgeCount(api, REQUESTER), 1);
	assert.deepEqual(api.calls, [['get', `/requests?status=open&all=false&limit=${LIST_LIMIT}`]]);
	const broken = fakeApi(() => fail('internal_error', 'Database is locked'));
	assert.equal(await fetchBadgeCount(broken, REQUESTER), null);
	assert.equal(await fetchBadgeCount(broken, APPROVER), null);
});

test('createRequest posts the body and returns the envelope data', async () => {
	const body = { source: 'civitai', model_id: '58390', version_id: '135867', file_id: '89277', note: 'please' };
	const api = fakeApi(() => ok({ request: request('r1', 'open'), already_requested: false }));
	const result = await createRequest(api, body);
	assert.equal(result.ok, true);
	assert.equal(result.data.request.id, 'r1');
	assert.deepEqual(api.calls, [['post', '/requests', body]]);
});

test('createRequest reports the error codes of the server', async () => {
	const available = await createRequest(fakeApi(() => fail('already_available', 'This file is already in your models')), {});
	assert.deepEqual(available, {
		ok: false,
		error: { status: 409, code: 'already_available', message: 'This file is already in your models', retryAfter: null }
	});
	const forbidden = await createRequest(fakeApi(() => fail('forbidden', "Your permissions do not allow requests from 'openmodeldb'")), {});
	assert.equal(forbidden.error.status, 403);
	const thrown = await createRequest(
		fakeApi(() => {
			throw new TypeError('Failed to fetch');
		}),
		{}
	);
	assert.equal(thrown.error.code, 'network_error');
});

test('listRequests builds its query string', async () => {
	const api = fakeApi(() => ok({ requests: [], open_count: 0, can_approve: true }));
	await listRequests(api);
	await listRequests(api, { status: 'open', all: true, limit: 50 });
	await listRequests(api, { status: '', all: false });
	assert.deepEqual(api.calls, [['get', '/requests'], ['get', '/requests?status=open&all=true&limit=50'], ['get', '/requests?all=false']]);
});

test('approveRequest sends only what is set, to the encoded id', async () => {
	const api = fakeApi(() => ok({ request: request('r1', 'approved'), pending: {}, download: null, already_installed: false, already_queued: false }));
	await approveRequest(api, '01J/X');
	await approveRequest(api, 'r1', { note: '  enjoy  ', modelType: 'embedding', assignGroupId: 'g1' });
	await approveRequest(api, 'r1', { note: '   ', modelType: '' });
	assert.deepEqual(api.calls, [
		['post', '/requests/01J%2FX/approve', {}],
		['post', '/requests/r1/approve', { note: 'enjoy', model_type: 'embedding', assign_group_id: 'g1' }],
		['post', '/requests/r1/approve', {}]
	]);
});

test('approveRequest passes the refusals through', async () => {
	const notOpen = await approveRequest(fakeApi(() => fail('not_open', 'The request is already approved')), 'r1');
	assert.equal(notOpen.error.code, 'not_open');
	assert.equal(notOpen.error.status, 409);
	const hidden = await approveRequest(fakeApi(() => fail('not_visible_to_requester', 'alice may not see this NSFW model')), 'r1');
	assert.equal(hidden.error.status, 403);
	assert.equal(hidden.error.message, 'alice may not see this NSFW model');
	const quota = await approveRequest(fakeApi(() => fail('limit_exceeded', 'Monthly download limit reached (20 of 20)')), 'r1');
	assert.equal(quota.error.message, 'Monthly download limit reached (20 of 20)');
});

test('rejectRequest and cancelRequest', async () => {
	const api = fakeApi(() => ok({ request: request('r1', 'rejected') }));
	await rejectRequest(api, 'r1', { note: ' Too large ' });
	await rejectRequest(api, 'r2');
	await cancelRequest(api, 'r3');
	assert.deepEqual(api.calls, [
		['post', '/requests/r1/reject', { note: 'Too large' }],
		['post', '/requests/r2/reject', {}],
		['delete', '/requests/r3']
	]);
	assert.equal((await cancelRequest({}, 'r1')).error.code, 'no_api');
});

const DETAIL = { source: 'civitai', id: 58390, name: 'Detail Tweaker', mb_type: 'lora', nsfw: false, license: null };
const VERSION = { id: 135867, name: 'v1.0', early_access: false };
const FILE = { id: 89277, name: 'add_detail.safetensors', size_bytes: 37_861_176 };

test('initialRequestForm starts on the suggestion when the user may ask for it', () => {
	assert.deepEqual(initialRequestForm(LIMITED, DETAIL), { modelType: 'lora', note: '' });
	assert.deepEqual(initialRequestForm(REQUESTER, DETAIL), { modelType: 'lora', note: '' });
	assert.deepEqual(initialRequestForm(LIMITED, { ...DETAIL, mb_type: 'checkpoint' }), { modelType: '', note: '' });
	assert.deepEqual(initialRequestForm(REQUESTER, { ...DETAIL, mb_type: null }), { modelType: '', note: '' });
});

test('requestFormProblems checks the type, the note and the size', () => {
	const fine = requestFormProblems({ modelType: 'lora', note: '' }, { me: LIMITED, detail: DETAIL, file: FILE });
	assert.deepEqual(fine, { modelType: '', note: '', size: '' });
	assert.equal(
		requestFormProblems({ modelType: 'checkpoint', note: '' }, { me: LIMITED, detail: DETAIL, file: FILE }).modelType,
		'Your permissions do not allow requesting this model type'
	);
	assert.equal(
		requestFormProblems({ modelType: '', note: '' }, { me: LIMITED, detail: { ...DETAIL, mb_type: null }, file: FILE }).modelType,
		'Choose what to file this model as'
	);
	assert.equal(
		requestFormProblems({ modelType: '', note: '' }, { me: REQUESTER, detail: { ...DETAIL, mb_type: null }, file: FILE }).modelType,
		'Choose what to file this model as'
	);
	assert.equal(requestFormProblems({ modelType: 'vae', note: '' }, { me: REQUESTER, detail: { ...DETAIL, mb_type: null }, file: FILE }).modelType, '');
	assert.equal(
		requestFormProblems({ modelType: 'lora', note: 'x'.repeat(MAX_NOTE_LENGTH + 1) }, { me: REQUESTER, detail: DETAIL, file: FILE }).note,
		`At most ${MAX_NOTE_LENGTH} characters`
	);
	assert.equal(
		requestFormProblems({ modelType: 'lora', note: ` ${'x'.repeat(MAX_NOTE_LENGTH)} ` }, { me: REQUESTER, detail: DETAIL, file: FILE }).note,
		''
	);
	assert.match(
		requestFormProblems({ modelType: 'lora', note: '' }, { me: LIMITED, detail: DETAIL, file: { ...FILE, size_bytes: 2 * 1024 ** 3 } }).size,
		/at most 1 GB/
	);
});

test('buildRequestBody sends the ids, and the type and note when given', () => {
	assert.deepEqual(buildRequestBody({ detail: DETAIL, version: VERSION, file: FILE, form: { modelType: 'lora', note: '  for the portraits ' } }), {
		source: 'civitai',
		model_id: '58390',
		version_id: '135867',
		file_id: '89277',
		model_type: 'lora',
		note: 'for the portraits'
	});
	assert.deepEqual(buildRequestBody({ detail: DETAIL, version: VERSION, file: FILE, form: { modelType: '', note: ' ' } }), {
		source: 'civitai',
		model_id: '58390',
		version_id: '135867',
		file_id: '89277'
	});
});

test('requestWarnings adds NSFW to the download warnings', () => {
	assert.deepEqual(requestWarnings({ detail: DETAIL, version: VERSION, file: FILE }), []);
	const titles = requestWarnings({
		detail: { ...DETAIL, nsfw: true, gated: true, license: { summary: 'x', commercial_use: [] } },
		version: { ...VERSION, early_access: true },
		file: { ...FILE, auth_provider_id: 'civitai' }
	}).map((warning) => warning.title);
	assert.deepEqual(titles, ['NSFW', 'Early access', 'Needs a login', 'No commercial use']);
	assert.equal(requestWarnings({ detail: DETAIL, version: { ...VERSION, nsfw: true }, file: FILE })[0].title, 'NSFW');
});

test('requestTitle, requestMeta, requestedType and sourceHost', () => {
	const normal = normalizeRequest(request('r1', 'open'));
	assert.equal(requestTitle(normal), 'Detail Tweaker LoRA');
	assert.equal(requestTitle(normalizeRequest({ id: 'x', snapshot: { file_name: 'a.gguf' } })), 'a.gguf');
	assert.equal(requestTitle(null), 'Model');
	assert.deepEqual(requestMeta(normal, (id) => (id === 'civitai' ? 'CivitAI' : id)), ['v1.0', 'add_detail.safetensors', '36.1 MB', 'LoRA', 'CivitAI']);
	assert.deepEqual(requestMeta(normalizeRequest({ id: 'x', source: 'huggingface', snapshot: { type: 'text-to-image' } })), ['text-to-image', 'huggingface']);
	assert.equal(requestedType(normal), 'lora');
	assert.equal(requestedType({ ...normal, model_type: 'embedding' }), 'embedding');
	assert.equal(requestedType(normalizeRequest({ id: 'x' })), '');
	assert.equal(sourceHost(normal), 'civitai.com');
	assert.equal(sourceHost(normalizeRequest({ id: 'x' })), '');
});

test('requestByline names who asked and when', () => {
	const normal = normalizeRequest(request('r1', 'open'));
	const time = parseTime(normal.created_at);
	const byline = requestByline(normal);
	assert.equal(byline.text, `Asked by alice · ${formatDate(time)}`);
	assert.equal(byline.title, new Date(time).toLocaleString());
	assert.equal(requestByline(normal, { showUser: false }).text, `Asked on ${formatDate(time)}`);
	assert.deepEqual(requestByline(normalizeRequest({ id: 'x' })), { text: 'Asked by an unknown user', title: '' });
	assert.equal(requestByline(normalizeRequest({ id: 'x' }), { showUser: false }).text, '');
});

test('decisionLine says who decided', () => {
	const decided = (status) => normalizeRequest(request('r1', status, { decided_by: 'u9', decided_by_username: 'bob' }));
	assert.equal(decisionLine(decided('approved')), 'Approved by bob');
	assert.equal(decisionLine(decided('done')), 'Approved by bob');
	assert.equal(decisionLine(decided('failed')), 'Approved by bob');
	assert.equal(decisionLine(decided('rejected')), 'Rejected by bob');
	assert.equal(decisionLine(normalizeRequest(request('r1', 'rejected'))), 'Rejected');
	assert.equal(decisionLine(decided('cancelled')), '');
	assert.equal(decisionLine(decided('open')), '');
});

test('the approve form: requested type first, a type needed when none, type sent only when changed', () => {
	const normal = normalizeRequest(request('r1', 'open'));
	const choice = approvalTypeChoice(normal);
	assert.equal(choice.selected, 'lora');
	assert.equal(choice.types[0], 'lora');
	assert.equal(choice.types.length, SERVED_MODEL_TYPES.length);
	const untyped = normalizeRequest({ id: 'r2', status: 'open' });
	assert.deepEqual(approvalTypeChoice(untyped), { types: SERVED_MODEL_TYPES, selected: '' });
	assert.deepEqual(approvalProblems(untyped, { modelType: '', note: '' }), { modelType: 'Choose what to file this model as', note: '' });
	assert.deepEqual(approvalProblems(normal, { modelType: '', note: '' }), { modelType: '', note: '' });
	assert.equal(approvalProblems(normal, { modelType: 'lora', note: 'x'.repeat(MAX_NOTE_LENGTH + 1) }).note, `At most ${MAX_NOTE_LENGTH} characters`);
	assert.deepEqual(approvalOptions(normal, { modelType: 'lora', note: ' ok ' }), { note: 'ok', modelType: '' });
	assert.deepEqual(approvalOptions(normal, { modelType: 'embedding', note: '' }), { note: '', modelType: 'embedding' });
	assert.deepEqual(approvalOptions(untyped, { modelType: 'vae' }), { note: '', modelType: 'vae' });
});

test('createToast for each answer of POST /requests', () => {
	assert.deepEqual(createToast({ ok: true, data: { request: {}, already_requested: false } }, FILE), {
		level: 'success',
		message: 'Request sent: an admin will review add_detail.safetensors',
		close: true
	});
	assert.deepEqual(createToast({ ok: true, data: { already_requested: true } }, { name: 'unet/model.safetensors' }), {
		level: 'info',
		message: 'You already asked for model.safetensors: your request is still open',
		close: true
	});
	assert.deepEqual(createToast({ ok: false, error: { code: 'already_available', message: 'x' } }, FILE), {
		level: 'info',
		message: 'add_detail.safetensors is already in your models',
		close: true
	});
	assert.deepEqual(createToast({ ok: false, error: { code: 'forbidden', message: 'No requests from OpenModelDB' } }, null), {
		level: 'error',
		message: 'Request refused: No requests from OpenModelDB',
		close: false
	});
});

test('approveToast, rejectToast and cancelToast', () => {
	const normal = normalizeRequest(request('r1', 'open'));
	assert.equal(approveToast({ ok: true, data: {} }, normal).message, 'Approved: downloading add_detail.safetensors for alice');
	assert.equal(
		approveToast({ ok: true, data: { already_installed: true } }, normal).message,
		'Approved: add_detail.safetensors was already installed, now assigned to alice'
	);
	assert.equal(approveToast({ ok: true, data: { already_queued: true } }, normal).level, 'info');
	assert.deepEqual(approveToast({ ok: false, error: { code: 'not_open', message: 'x' } }, normal), {
		level: 'info',
		message: 'This request was already handled',
		refresh: true
	});
	assert.deepEqual(approveToast({ ok: false, error: { code: 'limit_exceeded', message: 'Monthly limit reached' } }, normal), {
		level: 'error',
		message: 'Could not approve the request: Monthly limit reached',
		refresh: false
	});
	assert.equal(rejectToast({ ok: true, data: {} }, normal).message, 'Rejected the request of alice for add_detail.safetensors');
	assert.equal(rejectToast({ ok: false, error: { code: 'not_found', message: 'x' } }, normal).refresh, true);
	assert.equal(cancelToast({ ok: true, data: {} }, normal).message, 'Request cancelled: add_detail.safetensors');
	assert.equal(cancelToast({ ok: false, error: { code: 'forbidden', message: 'Not yours' } }, normal).message, 'Could not cancel the request: Not yours');
});

test('initialRequestForm starts on the file\'s own type, falling back to the repo type', () => {
	const repo = { ...DETAIL, mb_type: 'upscaler' };
	assert.equal(initialRequestForm(REQUESTER, repo, { ...FILE, file_type: 'Text Encoder' }).modelType, 'text_encoder');
	assert.equal(initialRequestForm(REQUESTER, repo, { ...FILE, file_type: 'UNet' }).modelType, 'unet');
	assert.equal(initialRequestForm(REQUESTER, repo, { ...FILE, file_type: 'VAE' }).modelType, 'vae');
	assert.equal(initialRequestForm(REQUESTER, repo, { ...FILE, file_type: 'LoRA' }).modelType, 'lora');
	assert.equal(initialRequestForm(REQUESTER, repo, { ...FILE, file_type: 'Model' }).modelType, 'upscaler');
	assert.equal(initialRequestForm(REQUESTER, repo, FILE).modelType, 'upscaler');
	assert.equal(initialRequestForm(REQUESTER, repo).modelType, 'upscaler');
});

test('suggestedModelType keeps the file type even when the user may not ask for it', () => {
	assert.equal(suggestedModelType({ mb_type: 'lora' }, { file_type: 'VAE' }), 'vae');
	assert.equal(suggestedModelType({ mb_type: 'lora' }, { file_type: ' text encoder ' }), 'text_encoder');
	assert.equal(suggestedModelType({ mb_type: null }, { file_type: 'Model' }), '');
	assert.equal(initialRequestForm(LIMITED, DETAIL, { ...FILE, file_type: 'VAE' }).modelType, '');
});

test('requestKey is stable across id types and field boundaries', () => {
	assert.equal(requestKey('civitai', 5, 6, 7), requestKey('civitai', '5', '6', '7'));
	assert.notEqual(requestKey('a', 'bc', 'd', 'e'), requestKey('ab', 'c', 'd', 'e'));
});

test('withPending adds a freshly created open request and ignores the rest', () => {
	const created = request('9', 'open', { source: 'huggingface', source_model_id: 'a/b', source_version_id: 'main', file_id: 'x.safetensors' });
	const next = withPending(new Set(), created);
	assert.ok(next.has(requestKey('huggingface', 'a/b', 'main', 'x.safetensors')));
	const keys = new Set();
	assert.equal(withPending(keys, request('9', 'done')), keys);
	assert.equal(withPending(keys, null), keys);
});

test('fetchPendingKeys asks for the caller\'s own open requests and returns null on failure', async () => {
	const calls = [];
	const api = fakeApi((method, path) => {
		calls.push([method, path]);
		const keys = Array.from({ length: 300 }, (_, index) => ({ source: 'civitai', model_id: '5', version_id: '6', file_id: String(index) }));
		return ok({ keys });
	});
	const keys = await fetchPendingKeys(api);
	assert.equal(keys.size, 300);
	assert.ok(keys.has(requestKey('civitai', '5', '6', '299')));
	assert.equal(calls[0][1], '/requests/pending');
	const failing = fakeApi(() => fail('internal_error', 'nope'));
	assert.equal(await fetchPendingKeys(failing), null);
});
