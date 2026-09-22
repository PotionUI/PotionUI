import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
	USER_MODE_ALL,
	USER_MODE_SELECTED,
	buildInitialFormState,
	toggleSelectedUser,
	clampLimit,
	buildFetchRequestBody,
	isFormValid,
	summarizeModelResult,
	summarizeFetchResult
} from './promptsFetch.js';

test('buildInitialFormState defaults to Selected users = the current admin only', () => {
	const state = buildInitialFormState(
		{ defaults: { sort: 'Most Reactions', period: 'AllTime', nsfw: null, limit: 20, include_showcase: true, include_negative: false } },
		'admin-1'
	);
	assert.equal(state.userMode, USER_MODE_SELECTED);
	assert.deepEqual(state.selectedUserIds, ['admin-1']);
	assert.equal(state.sort, 'Most Reactions');
	assert.equal(state.limit, 20);
	assert.equal(state.includeShowcase, true);
	assert.equal(state.includeNegative, false);
});

test('buildInitialFormState tolerates a missing currentUserId', () => {
	const state = buildInitialFormState({ defaults: { limit: 10 } }, null);
	assert.deepEqual(state.selectedUserIds, []);
});

test('toggleSelectedUser adds and removes immutably', () => {
	const initial = ['a', 'b'];
	const added = toggleSelectedUser(initial, 'c');
	assert.deepEqual(added, ['a', 'b', 'c']);
	assert.deepEqual(initial, ['a', 'b']);

	const removed = toggleSelectedUser(added, 'b');
	assert.deepEqual(removed, ['a', 'c']);
});

test('clampLimit clamps to [1, limitMax] and tolerates bad input', () => {
	assert.equal(clampLimit(50, 200), 50);
	assert.equal(clampLimit(0, 200), 1);
	assert.equal(clampLimit(-5, 200), 1);
	assert.equal(clampLimit(500, 200), 200);
	assert.equal(clampLimit('abc', 200), 1);
	assert.equal(clampLimit(50, 0), 50);
	assert.equal(clampLimit(500, 0), 200);
});

test('buildFetchRequestBody sends user_ids null for "All users"', () => {
	const body = buildFetchRequestBody('model-1', {
		userMode: USER_MODE_ALL,
		selectedUserIds: ['ignored'],
		sort: 'Newest',
		period: 'Month',
		nsfw: 'None',
		limit: 30,
		includeShowcase: false,
		includeNegative: true
	});
	assert.deepEqual(body, {
		model_ids: ['model-1'],
		user_ids: null,
		sort: 'Newest',
		period: 'Month',
		nsfw: 'None',
		limit: 30,
		include_showcase: false,
		include_negative: true
	});
});

test('buildFetchRequestBody sends the selected user ids for "Selected users"', () => {
	const body = buildFetchRequestBody('model-1', {
		userMode: USER_MODE_SELECTED,
		selectedUserIds: ['u1', 'u2'],
		sort: 'Newest',
		period: 'Month',
		nsfw: null,
		limit: 30,
		includeShowcase: false,
		includeNegative: false
	});
	assert.deepEqual(body.user_ids, ['u1', 'u2']);
});

test('isFormValid requires at least one selected user unless "All users"', () => {
	assert.equal(isFormValid({ userMode: USER_MODE_ALL, selectedUserIds: [] }), true);
	assert.equal(isFormValid({ userMode: USER_MODE_SELECTED, selectedUserIds: [] }), false);
	assert.equal(isFormValid({ userMode: USER_MODE_SELECTED, selectedUserIds: ['u1'] }), true);
});

test('summarizeModelResult flags model_not_linked without treating it as a generic error', () => {
	const summary = summarizeModelResult(
		{ model_id: 'm1', model_name: 'Model One', error: 'model_not_linked' },
		['u1']
	);
	assert.equal(summary.notLinked, true);
	assert.equal(summary.error, null);
});

test('summarizeModelResult carries a generic error through untouched', () => {
	const summary = summarizeModelResult(
		{ model_id: 'm1', model_name: 'Model One', error: 'civitai_unavailable' },
		['u1']
	);
	assert.equal(summary.notLinked, false);
	assert.equal(summary.error, 'civitai_unavailable');
});

test('summarizeModelResult only builds per-user rows for more than one user', () => {
	const single = summarizeModelResult(
		{ model_id: 'm1', model_name: 'Model One', images_seen: 5, with_prompt: 3, created: 3, skipped_duplicates: 0, per_user: { u1: { created: 3, skipped_duplicates: 0 } } },
		['u1']
	);
	assert.deepEqual(single.perUserRows, []);

	const multi = summarizeModelResult(
		{
			model_id: 'm1',
			model_name: 'Model One',
			images_seen: 9,
			with_prompt: 6,
			created: 6,
			skipped_duplicates: 1,
			per_user: { u1: { created: 4, skipped_duplicates: 1 }, u2: { created: 2, skipped_duplicates: 0 } }
		},
		['u1', 'u2']
	);
	assert.equal(multi.perUserRows.length, 2);
	assert.deepEqual(multi.perUserRows.find((r) => r.userId === 'u1'), { userId: 'u1', created: 4, skippedDuplicates: 1 });
});

test('summarizeFetchResult maps the full payload and tolerates missing arrays', () => {
	const result = summarizeFetchResult({
		users: ['u1'],
		models: [{ model_id: 'm1', model_name: 'Model One', images_seen: 2, with_prompt: 1, created: 1, skipped_duplicates: 0 }]
	});
	assert.equal(result.users.length, 1);
	assert.equal(result.models.length, 1);
	assert.equal(result.models[0].modelName, 'Model One');

	const empty = summarizeFetchResult(null);
	assert.deepEqual(empty, { users: [], models: [] });
});
