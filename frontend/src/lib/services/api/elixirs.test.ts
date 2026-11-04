import { describe, it, expect, vi } from 'vitest';
import type { AxiosInstance } from 'axios';

vi.mock('$lib/services/api/index', () => ({ api: { getClient: () => ({}) } }));

import { createElixirsApi } from './elixirs';

interface Seen {
	method: string;
	url: string;
	data: unknown;
}

function axiosReturning(reply: unknown = {}, fail = false) {
	const calls: Seen[] = [];
	const request = vi.fn(async (config: { method: string; url: string; data?: unknown; validateStatus?: (status: number) => boolean }) => {
		calls.push({ method: config.method, url: config.url, data: config.data });
		if (fail) throw new TypeError('offline');
		return { status: 200, data: typeof reply === 'function' ? reply(config) : reply };
	});
	return { calls, request, client: { request } as unknown as AxiosInstance };
}

describe('elixirs api', () => {
	it('session mode changes post to the node session endpoints', async () => {
		const { calls, client } = axiosReturning({ revision: 3 });
		const elixirs = createElixirsApi(client);
		await elixirs.followSession('e1', 'n1', { expected_revision: 2, session_id: 's1', mode: 'i2v' });
		await elixirs.copySession('e1', 'n1', { expected_revision: 3 });
		await elixirs.ownSettings('e1', 'n1', { expected_revision: 4 });
		await elixirs.followedSessions('e1');
		expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
			'POST /api/elixirs/e1/nodes/n1/session/follow',
			'POST /api/elixirs/e1/nodes/n1/session/copy',
			'POST /api/elixirs/e1/nodes/n1/session/new',
			'GET /api/elixirs/e1/followed-sessions'
		]);
		expect(calls[0].data).toEqual({ expected_revision: 2, session_id: 's1', mode: 'i2v' });
	});

	it('a refused mode change keeps its code', async () => {
		const { client } = axiosReturning({ detail: { code: 'not_following', message: 'Orbit does not follow a session.' } });
		const result = await createElixirsApi(client).copySession('e1', 'n1', { expected_revision: 1 });
		expect(result.ok).toBe(false);
		if (!result.ok) expect(result.error.code).toBe('not_following');
	});

	it('saving a session posts JSON to the app and returns the new session', async () => {
		const { calls, client } = axiosReturning({ success: true, data: { id: 's9', name: 'Gold restyle', version: 1 } });
		const result = await createElixirsApi(client).saveSession({ preset_id: 'qwen', name: 'Gold restyle', data: { edit: { prompt: 'gold' } } });
		expect(result).toEqual({ ok: true, data: { id: 's9', name: 'Gold restyle', version: 1 } });
		expect(calls[0].url).toBe('/api/sessions/save');
		expect(calls[0].method).toBe('POST');
		expect(calls[0].data).toEqual({ preset_id: 'qwen', name: 'Gold restyle', data: { edit: { prompt: 'gold' } } });
	});

	it('a session save the app refuses is an error', async () => {
		const refused = await createElixirsApi(axiosReturning({ success: false, message: 'Session creation blocked' }).client).saveSession({});
		expect(refused.ok).toBe(false);
		if (!refused.ok) expect(refused.error.message).toBe('Session creation blocked');
		const failed = await createElixirsApi(axiosReturning({}, true).client).saveSession({});
		expect(failed.ok).toBe(false);
		if (!failed.ok) expect(failed.error.code).toBe('network');
	});

	it('every elixir and run call goes under /api/elixirs and never throws on an error status', async () => {
		const { calls, client } = axiosReturning({});
		const elixirs = createElixirsApi(client);
		await elixirs.listElixirs();
		await elixirs.createElixir('Potion study');
		await elixirs.getElixir('e1');
		await elixirs.updateElixir('e1', { name: 'x' });
		await elixirs.deleteElixir('e1');
		await elixirs.validateElixir('e1');
		await elixirs.importSession('e1', 'n1', {});
		await elixirs.listPresets();
		await elixirs.startBrew('e1', {});
		await elixirs.runNode('e1', 'n1', {});
		await elixirs.listRuns('e1');
		await elixirs.getRun('r1');
		await elixirs.saveSelection('r1', 'x1', {});
		await elixirs.continueEdge('r1', 'x1', {});
		await elixirs.editText('r1', 't1', {});
		await elixirs.cancelRun('r1');
		await elixirs.retryFailed('r1', 'n1');
		expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
			'GET /api/elixirs',
			'POST /api/elixirs',
			'GET /api/elixirs/e1',
			'PUT /api/elixirs/e1',
			'DELETE /api/elixirs/e1',
			'POST /api/elixirs/e1/validate',
			'POST /api/elixirs/e1/nodes/n1/session',
			'GET /api/elixirs/presets',
			'POST /api/elixirs/e1/runs',
			'POST /api/elixirs/e1/nodes/n1/runs',
			'GET /api/elixirs/e1/runs',
			'GET /api/elixirs/runs/r1',
			'PUT /api/elixirs/runs/r1/edges/x1/selection',
			'POST /api/elixirs/runs/r1/edges/x1/continue',
			'PUT /api/elixirs/runs/r1/texts/t1',
			'POST /api/elixirs/runs/r1/cancel',
			'POST /api/elixirs/runs/r1/nodes/n1/retry-failed'
		]);
		expect(calls[1].data).toEqual({ name: 'Potion study', run_mode: 'review' });
	});

	it('a list validation error reads its first message', async () => {
		const { client } = axiosReturning({ detail: [{ msg: 'name too long' }] });
		const result = await createElixirsApi(client).updateElixir('e1', { name: 'x' });
		expect(result).toEqual({ ok: false, error: { code: 'invalid', message: 'The change was refused: name too long' } });
	});

	it('app reads unwrap the envelope', async () => {
		const sessions = await createElixirsApi(axiosReturning({ success: true, data: [{ id: 's1' }] }).client).listSessions('krea 2');
		expect(sessions).toEqual({ ok: true, data: [{ id: 's1' }] });
		const { calls, client } = axiosReturning({ success: true, data: { vars: { a: 1 } } });
		const vars = await createElixirsApi(client).presetVars('krea');
		expect(vars).toEqual({ ok: true, data: { a: 1 } });
		expect(calls[0].url).toBe('/api/presets/krea');
		const refused = await createElixirsApi(axiosReturning({ success: false }).client).listSessions('p');
		expect(refused.ok).toBe(false);
		const versions = await createElixirsApi(axiosReturning({ data: [{ version_number: 2 }] }).client).listVersions('s1');
		expect(versions).toEqual({ ok: true, data: [{ version_number: 2 }] });
	});
});
