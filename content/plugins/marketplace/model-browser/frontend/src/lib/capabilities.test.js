import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
	allowedTypes,
	can,
	canChooseGroup,
	canChooseRemote,
	canDownloadFrom,
	canRequestFrom,
	exceedsSizeLimit,
	fileAction,
	fileActionInfo,
	groupChoices,
	normalizeMe,
	paramsFor,
	requestBlock,
	requestBlockReason,
	sizeLimitBytes
} from './capabilities.js';
import { SERVED_MODEL_TYPES, installHostModelTypes } from './testModelTypes.js';

installHostModelTypes();

function meData(overrides = {}) {
	return {
		user: { id: 'u1', username: 'alice', is_admin: false },
		capabilities: ['browse', 'request'],
		params: { sources: [], model_types: [], max_file_gb: null, assign_to: 'user', allow_remote: false, unrestricted: false },
		nsfw: { ceiling: 'allowed', mode: 'blur', allowed_modes: ['blur', 'show', 'hide'], max_level: 2, hide_poi: false, setting_key: 'media_nsfw_filter_mode' },
		...overrides
	};
}

const ADMIN = normalizeMe(
	meData({
		user: { id: 'a1', username: 'root', is_admin: true },
		capabilities: ['approve', 'browse', 'download', 'request'],
		params: { sources: [], model_types: [], max_file_gb: null, assign_to: 'user', allow_remote: false, unrestricted: true },
		groups: [{ id: 'all_admins', name: 'All Admins' }]
	})
);

const DOWNLOADER = normalizeMe(
	meData({
		capabilities: ['browse', 'download', 'request'],
		params: {
			sources: ['civitai', 'huggingface'],
			model_types: ['embedding', 'lora'],
			max_file_gb: 2,
			assign_to: 'group',
			allow_remote: true,
			unrestricted: false
		},
		groups: [{ id: 'g1', name: 'Artists' }, { id: 'g2' }]
	})
);

const BROWSER = normalizeMe(meData({ capabilities: ['browse'] }));
const REQUESTER = normalizeMe(meData({ params: { sources: ['civitai', 'huggingface', 'openmodeldb'], model_types: [], max_file_gb: null } }));

test('normalizeMe keeps a well formed answer', () => {
	assert.deepEqual(DOWNLOADER.user, { id: 'u1', username: 'alice', is_admin: false });
	assert.deepEqual(DOWNLOADER.capabilities, ['browse', 'download', 'request']);
	assert.deepEqual(DOWNLOADER.params, {
		sources: ['civitai', 'huggingface'],
		model_types: ['embedding', 'lora'],
		max_file_gb: 2,
		assign_to: 'group',
		allow_remote: true,
		unrestricted: false
	});
	assert.deepEqual(DOWNLOADER.nsfw, {
		ceiling: 'allowed',
		mode: 'blur',
		allowed_modes: ['blur', 'show', 'hide'],
		max_level: 2,
		hide_poi: false,
		setting_key: 'media_nsfw_filter_mode'
	});
	assert.deepEqual(DOWNLOADER.groups, [{ id: 'g1', name: 'Artists' }, { id: 'g2', name: 'g2' }]);
});

test('normalizeMe falls back to the most restrictive values', () => {
	const me = normalizeMe(null);
	assert.deepEqual(me.capabilities, []);
	assert.equal(me.params.unrestricted, false);
	assert.equal(me.params.allow_remote, false);
	assert.equal(me.params.max_file_gb, null);
	assert.equal(me.params.assign_to, 'user');
	assert.equal(me.nsfw.ceiling, 'blocked');
	assert.equal(me.nsfw.mode, 'hide');
	assert.deepEqual(me.nsfw.allowed_modes, ['hide']);
	assert.deepEqual(me.groups, []);
	assert.equal(me.nsfw.setting_key, 'media_nsfw_filter_mode');
});

test('normalizeMe drops unknown capabilities, bad groups and bad sizes', () => {
	const me = normalizeMe(
		meData({
			capabilities: ['browse', 'manage', 7, 'browse'],
			params: { max_file_gb: -3, assign_to: 'everyone' },
			groups: [null, { name: 'no id' }, { id: 5, name: 'Five' }, 'x']
		})
	);
	assert.deepEqual(me.capabilities, ['browse']);
	assert.equal(me.params.max_file_gb, null);
	assert.equal(me.params.assign_to, 'user');
	assert.deepEqual(me.groups, [{ id: '5', name: 'Five' }]);
});

test('normalizeMe bounds the NSFW mode by the modes the ceiling allows', () => {
	const blocked = normalizeMe(meData({ nsfw: { ceiling: 'blocked', mode: 'show', allowed_modes: ['blur', 'show'] } }));
	assert.equal(blocked.nsfw.mode, 'hide');
	assert.deepEqual(blocked.nsfw.allowed_modes, ['hide']);

	const blur = normalizeMe(meData({ nsfw: { ceiling: 'blur', mode: 'show', allowed_modes: ['blur', 'hide'] } }));
	assert.equal(blur.nsfw.mode, 'blur');

	const odd = normalizeMe(meData({ nsfw: { ceiling: 'sideways', mode: 'show', allowed_modes: ['show'] } }));
	assert.equal(odd.nsfw.ceiling, 'blocked');
	assert.equal(odd.nsfw.mode, 'hide');
});

test('can: admins hold everything, others what /me lists', () => {
	for (const capability of ['browse', 'request', 'download', 'approve']) assert.equal(can(ADMIN, capability), true);
	assert.equal(can(DOWNLOADER, 'download'), true);
	assert.equal(can(DOWNLOADER, 'approve'), false);
	assert.equal(can(BROWSER, 'browse'), true);
	assert.equal(can(BROWSER, 'request'), false);
	assert.equal(can(null, 'browse'), false);
	assert.equal(can(normalizeMe({}), 'browse'), false);
});

test('allowedTypes offers every type to an admin, the suggestion first and selected', () => {
	const result = allowedTypes(ADMIN, 'lora');
	assert.equal(result.types.length, SERVED_MODEL_TYPES.length);
	assert.equal(result.types[0], 'lora');
	assert.equal(result.selected, 'lora');
	assert.equal(result.suggestionAllowed, true);
});

test('allowedTypes limits a restricted user to their types', () => {
	assert.deepEqual(allowedTypes(DOWNLOADER, 'lora'), { types: ['lora', 'embedding'], selected: 'lora', suggestionAllowed: true });
	assert.deepEqual(allowedTypes(DOWNLOADER, 'checkpoint'), { types: ['lora', 'embedding'], selected: '', suggestionAllowed: false });
});

test('allowedTypes makes the user choose when the source has no suggestion', () => {
	assert.deepEqual(allowedTypes(DOWNLOADER, null), { types: ['lora', 'embedding'], selected: '', suggestionAllowed: false });
	const everyType = allowedTypes(REQUESTER, null);
	assert.equal(everyType.types.length, SERVED_MODEL_TYPES.length);
	assert.equal(everyType.selected, '');
});

test('allowedTypes keeps a listed type the page does not know yet', () => {
	const me = normalizeMe(meData({ capabilities: ['download'], params: { model_types: ['lora', 'hologram'] } }));
	assert.deepEqual(allowedTypes(me, 'hologram').types, ['hologram', 'lora']);
});

test('canChooseGroup and canChooseRemote', () => {
	assert.equal(canChooseGroup(ADMIN), true);
	assert.equal(canChooseGroup(DOWNLOADER), true);
	assert.equal(canChooseGroup(REQUESTER), false);
	assert.equal(canChooseRemote(ADMIN), true);
	assert.equal(canChooseRemote(DOWNLOADER), true);
	assert.equal(canChooseRemote(BROWSER), false);
	assert.equal(canChooseRemote(null), false);
});

test('fileAction depends on the capabilities and the sources allowed', () => {
	assert.equal(fileAction(ADMIN, 'openmodeldb'), 'download');
	assert.equal(fileAction(DOWNLOADER, 'civitai'), 'download');
	assert.equal(fileAction(DOWNLOADER, 'openmodeldb'), 'source-blocked');
	assert.equal(fileAction(REQUESTER, 'civitai'), 'request');
	assert.equal(fileAction(BROWSER, 'civitai'), 'none');
	assert.equal(canDownloadFrom(DOWNLOADER, 'huggingface'), true);
	assert.equal(canDownloadFrom(REQUESTER, 'civitai'), false);
});

test('fileAction and canRequestFrom: requests are bounded by the sources of the parameters', () => {
	const civitaiOnly = normalizeMe(meData({ params: { sources: ['civitai'] } }));
	assert.equal(fileAction(civitaiOnly, 'civitai'), 'request');
	assert.equal(fileAction(civitaiOnly, 'huggingface'), 'request-blocked');
	assert.equal(canRequestFrom(civitaiOnly, 'huggingface'), false);
	assert.equal(canRequestFrom(REQUESTER, 'huggingface'), true);
	assert.equal(canRequestFrom(REQUESTER, 'unknown-source'), false);
	assert.equal(fileAction(normalizeMe(meData()), 'civitai'), 'request-blocked');
	assert.equal(canRequestFrom(ADMIN, 'unknown-source'), true);
	assert.equal(canRequestFrom(BROWSER, 'civitai'), false);
	assert.equal(canRequestFrom(null, 'civitai'), false);
});

const SPLIT = normalizeMe(
	meData({
		capabilities: ['browse', 'download', 'request'],
		params: { sources: ['civitai'], model_types: ['lora'], max_file_gb: 2, assign_to: 'user', allow_remote: true, unrestricted: false },
		request_params: { sources: ['civitai', 'huggingface'], model_types: ['embedding', 'checkpoint'], max_file_gb: 10, assign_to: 'group' }
	})
);
const ASKER = normalizeMe(
	meData({
		capabilities: ['browse', 'request'],
		params: { sources: [], model_types: [], max_file_gb: null },
		request_params: { sources: ['civitai'], model_types: ['lora'], max_file_gb: 1 }
	})
);

test('normalizeMe reads request_params apart from params', () => {
	assert.deepEqual(SPLIT.request_params, {
		sources: ['civitai', 'huggingface'],
		model_types: ['embedding', 'checkpoint'],
		max_file_gb: 10,
		assign_to: 'group',
		unrestricted: false
	});
	assert.deepEqual(SPLIT.params, {
		sources: ['civitai'],
		model_types: ['lora'],
		max_file_gb: 2,
		assign_to: 'user',
		allow_remote: true,
		unrestricted: false
	});
	assert.equal('allow_remote' in SPLIT.request_params, false);
});

test('normalizeMe falls back to params when request_params is missing, and cleans a malformed one', () => {
	assert.deepEqual(DOWNLOADER.request_params, {
		sources: ['civitai', 'huggingface'],
		model_types: ['embedding', 'lora'],
		max_file_gb: 2,
		assign_to: 'group',
		unrestricted: false
	});
	const odd = normalizeMe(meData({ request_params: { sources: 'civitai', model_types: [3, 'lora'], max_file_gb: 0, assign_to: 'team' } }));
	assert.deepEqual(odd.request_params, { sources: [], model_types: ['lora'], max_file_gb: null, assign_to: 'user', unrestricted: false });
	assert.equal(normalizeMe(meData({ request_params: 'all' })).request_params.max_file_gb, null);
	assert.equal(ADMIN.request_params.unrestricted, true);
	assert.deepEqual(normalizeMe(null).request_params, { sources: [], model_types: [], max_file_gb: null, assign_to: 'user', unrestricted: false });
});

test('paramsFor, allowedTypes and the size limit per purpose', () => {
	assert.equal(paramsFor(SPLIT), SPLIT.params);
	assert.equal(paramsFor(SPLIT, 'request'), SPLIT.request_params);
	assert.equal(paramsFor({ params: SPLIT.params }, 'request'), SPLIT.params);
	assert.equal(paramsFor(null, 'request'), null);
	assert.deepEqual(allowedTypes(SPLIT, 'lora'), { types: ['lora'], selected: 'lora', suggestionAllowed: true });
	assert.deepEqual(allowedTypes(SPLIT, 'lora', { purpose: 'request' }), {
		types: ['checkpoint', 'embedding'],
		selected: '',
		suggestionAllowed: false
	});
	assert.deepEqual(allowedTypes(SPLIT, 'embedding', { purpose: 'request' }).types, ['embedding', 'checkpoint']);
	assert.equal(sizeLimitBytes(SPLIT), 2 * 1024 ** 3);
	assert.equal(sizeLimitBytes(SPLIT, { purpose: 'request' }), 10 * 1024 ** 3);
	assert.equal(exceedsSizeLimit(SPLIT, 5 * 1024 ** 3), true);
	assert.equal(exceedsSizeLimit(SPLIT, 5 * 1024 ** 3, { purpose: 'request' }), false);
	assert.equal(sizeLimitBytes(ADMIN, { purpose: 'request' }), null);
});

test('requestBlock and canRequestFrom read the request parameters: source, suggested type, size', () => {
	assert.equal(canRequestFrom(SPLIT, 'huggingface'), true);
	assert.equal(canDownloadFrom(SPLIT, 'huggingface'), false);
	assert.equal(requestBlock(ASKER, { source: 'civitai', type: 'lora', sizeBytes: 500 * 1024 ** 2 }), '');
	assert.equal(requestBlock(ASKER, { source: 'huggingface', type: 'lora' }), 'source');
	assert.equal(requestBlock(ASKER, { source: 'civitai', type: 'checkpoint' }), 'type');
	assert.equal(requestBlock(ASKER, { source: 'civitai', type: null }), '');
	assert.equal(requestBlock(ASKER, { source: 'civitai', type: 'lora', sizeBytes: 2 * 1024 ** 3 }), 'size');
	assert.equal(requestBlock(ASKER, { source: 'civitai', type: 'lora', sizeBytes: null }), '');
	assert.equal(requestBlock(BROWSER, { source: 'civitai' }), 'capability');
	assert.equal(requestBlock(ADMIN, { source: 'openmodeldb', type: 'vae', sizeBytes: 10 ** 12 }), '');
	assert.equal(requestBlockReason(ASKER, { source: 'huggingface' }, 'Hugging Face'), 'Not allowed from Hugging Face');
	assert.equal(requestBlockReason(ASKER, { source: 'civitai', type: 'checkpoint' }), 'Base model not allowed');
	assert.equal(requestBlockReason(ASKER, { source: 'civitai', type: 'lora', sizeBytes: 2 * 1024 ** 3 }), 'Over your 1 GB limit');
	assert.equal(requestBlockReason(ASKER, { source: 'civitai', type: 'lora' }), '');
});

test('fileAction: downloads by params, requests by request_params', () => {
	assert.equal(fileAction(SPLIT, 'civitai', { type: 'lora' }), 'download');
	assert.equal(fileAction(SPLIT, 'huggingface', { type: 'checkpoint', sizeBytes: 6 * 1024 ** 3 }), 'request');
	assert.equal(fileAction(SPLIT, 'huggingface', { type: 'lora' }), 'source-blocked');
	assert.equal(fileAction(SPLIT, 'openmodeldb'), 'source-blocked');
	assert.equal(fileAction(ASKER, 'civitai', { type: 'lora', sizeBytes: 100 }), 'request');
	assert.equal(fileAction(ASKER, 'civitai', { type: 'vae' }), 'request-blocked');
	assert.equal(fileAction(ASKER, 'civitai', { type: 'lora', sizeBytes: 2 * 1024 ** 3 }), 'request-blocked');
	assert.equal(fileAction(ASKER, 'huggingface'), 'request-blocked');
	assert.deepEqual(fileActionInfo(SPLIT, 'openmodeldb', {}, 'OpenModelDB'), { action: 'source-blocked', reason: 'Not allowed from OpenModelDB' });
	assert.deepEqual(fileActionInfo(ASKER, 'civitai', { type: 'vae' }, 'CivitAI'), { action: 'request-blocked', reason: 'VAE not allowed' });
	assert.deepEqual(fileActionInfo(ASKER, 'civitai', { type: 'lora' }, 'CivitAI'), { action: 'request', reason: '' });
	assert.deepEqual(fileActionInfo(BROWSER, 'civitai'), { action: 'none', reason: '' });
});

test('normalizeMe keeps the open request count, null when absent or malformed', () => {
	assert.deepEqual(normalizeMe(meData({ requests: { open_count: 4 } })).requests, { open_count: 4 });
	assert.deepEqual(normalizeMe(meData({ requests: { open_count: 2.9 } })).requests, { open_count: 2 });
	assert.deepEqual(normalizeMe(meData()).requests, { open_count: null });
	assert.deepEqual(normalizeMe(meData({ requests: { open_count: -1 } })).requests, { open_count: null });
	assert.deepEqual(normalizeMe(meData({ requests: { open_count: '3' } })).requests, { open_count: null });
	assert.deepEqual(normalizeMe(null).requests, { open_count: null });
});

test('size limits apply to restricted users only, in GiB', () => {
	assert.equal(sizeLimitBytes(ADMIN), null);
	assert.equal(sizeLimitBytes(DOWNLOADER), 2 * 1024 ** 3);
	assert.equal(exceedsSizeLimit(DOWNLOADER, 2 * 1024 ** 3), false);
	assert.equal(exceedsSizeLimit(DOWNLOADER, 2 * 1024 ** 3 + 1), true);
	assert.equal(exceedsSizeLimit(DOWNLOADER, null), false);
	assert.equal(exceedsSizeLimit(ADMIN, 10 ** 13), false);
	assert.equal(exceedsSizeLimit(REQUESTER, 10 ** 13), false);
});

test('groupChoices puts the user first', () => {
	assert.deepEqual(groupChoices(DOWNLOADER), [
		{ id: '', name: 'Me' },
		{ id: 'g1', name: 'Artists' },
		{ id: 'g2', name: 'g2' }
	]);
	assert.deepEqual(groupChoices(BROWSER), [{ id: '', name: 'Me' }]);
});

test('allowedTypes offers the types the host serves, plugin types included', () => {
	const result = allowedTypes(ADMIN, null);
	assert.deepEqual(result.types, SERVED_MODEL_TYPES);
	assert.ok(result.types.includes('pose_rig'));
	assert.ok(!result.types.includes('cloud'));
});

test('allowedTypes offers nothing but the listed types before the host has served any', () => {
	const saved = globalThis.__potionui;
	globalThis.__potionui = {};
	try {
		assert.deepEqual(allowedTypes(ADMIN, 'lora').types, []);
	} finally {
		globalThis.__potionui = saved;
	}
});
