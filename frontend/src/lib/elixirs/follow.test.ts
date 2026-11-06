import { expect, test } from 'vitest';
import {
	FOLLOW_ISSUE_CODES,
	SESSION_MODES,
	cardMarker,
	conversions,
	effectiveSettings,
	followedStatus,
	modeExplanation,
	sessionHashes,
	sessionMode,
	sessionProblem,
	sessionRow,
	staleReasonText,
	templateSource,
	uniqueSessionName
} from './follow';
import type { ElixirNode } from './types';

const followed = (extra: Partial<ElixirNode> = {}): ElixirNode => ({
	id: 'n1',
	kind: 'preset',
	preset_id: 'wan',
	mode: 'i2v',
	session_mode: 'follow',
	session_ref: { id: 's1', name: 'Bottle orbit', version: 3, deleted: false },
	session_snapshot: { prompt: 'last seen', formData: { steps: 20 } },
	session_hash: 'h-old',
	overrides: {},
	...extra
});

const copied = (extra: Partial<ElixirNode> = {}): ElixirNode => ({
	id: 'n2',
	kind: 'preset',
	preset_id: 'qwen',
	mode: 'edit',
	session_mode: 'copy',
	session_source: { id: 's2', name: 'Gold restyle', version: 3 },
	session_snapshot: { prompt: 'gold', formData: { steps: 28, guidance: 3.5 } },
	overrides: { formData: { steps: 12 } },
	...extra
});

const own = (extra: Partial<ElixirNode> = {}): ElixirNode => ({
	id: 'n3',
	kind: 'preset',
	preset_id: 'krea',
	mode: 't2i',
	session_mode: 'new',
	session_source: null,
	session_snapshot: null,
	overrides: { prompt: 'a bottle', formData: { steps: 8 } },
	...extra
});

const ok = { status: 'ok', session: { id: 's1', name: 'Bottle orbit', version: 4 }, settings: { prompt: 'fresh', formData: { steps: 30 } }, session_hash: 'h-new' };

test('the session mode is read from the node, and derived for old nodes', () => {
	expect(SESSION_MODES.map((item) => item.id)).toStrictEqual(['follow', 'copy', 'new']);
	expect(SESSION_MODES.map((item) => item.label)).toStrictEqual(['Follow', 'Copy', 'New']);
	expect(sessionMode(followed())).toBe('follow');
	expect(sessionMode(copied())).toBe('copy');
	expect(sessionMode(own())).toBe('new');
	expect(sessionMode({ kind: 'preset', session_source: { id: 's' } })).toBe('copy');
	expect(sessionMode({ kind: 'preset', session_source: null })).toBe('new');
	expect(sessionMode({ kind: 'preset', session_mode: 'bogus' })).toBe('new');
	expect(sessionMode({ kind: 'llm' })).toBe(null);
});

test('each mode has one line that says where its settings live', () => {
	expect(modeExplanation('follow')).toMatch(/live in the session/);
	expect(modeExplanation('copy')).toMatch(/ignored until you update/);
	expect(modeExplanation('new')).toMatch(/own settings/);
});

test('a followed node takes its status from the fetched state, then from the stored ref, then from issues', () => {
	expect(followedStatus(followed(), ok)).toBe('ok');
	expect(followedStatus(followed(), { status: 'mismatch' })).toBe('mismatch');
	expect(followedStatus(followed({ session_ref: { id: 's1', name: 'x', deleted: true } }), undefined)).toBe('deleted');
	expect(followedStatus(followed(), undefined, [{ code: 'followed_session_unavailable' }])).toBe('unavailable');
	expect(followedStatus(followed(), undefined, [{ code: 'followed_session_mode_missing' }])).toBe('mode_missing');
	expect(followedStatus(followed(), undefined)).toBe('ok');
	expect(followedStatus(copied(), { status: 'deleted' })).toBe(null);
	expect([...FOLLOW_ISSUE_CODES].sort()).toStrictEqual(['followed_session_deleted', 'followed_session_mismatch', 'followed_session_mode_missing', 'followed_session_unavailable']);
});

test('the card marker names the session and its state', () => {
	expect(cardMarker(followed(), ok)).toStrictEqual({ text: 'Following Bottle orbit', tone: 'signal', icon: 'plug' });
	expect(cardMarker(copied())).toStrictEqual({ text: 'Copy of Gold restyle', tone: 'muted', icon: 'document' });
	expect(cardMarker(own())).toStrictEqual({ text: 'Own settings', tone: 'subtle', icon: null });
	expect(cardMarker(followed(), { status: 'deleted' })).toStrictEqual({ text: 'Session was deleted', tone: 'warning', icon: 'warning' });
	expect(cardMarker(followed(), { status: 'unavailable' })!.text).toBe('Session not available');
	expect(cardMarker(followed(), { status: 'mismatch' })!.text).toBe('Session is for another preset');
	expect(cardMarker(followed(), { status: 'mode_missing' })!.text).toBe('Session has no settings for this mode');
	expect(cardMarker(followed(), ok)!.text).toBe('Following Bottle orbit');
	expect(cardMarker({ ...followed(), session_ref: { id: 's1', name: null } }, undefined)!.text).toBe('Following a session');
	expect(cardMarker({ kind: 'preset', preset_id: null })).toBe(null);
	expect(cardMarker({ kind: 'llm' })).toBe(null);
});

test('a broken follow raises a problem with the way out', () => {
	expect(sessionProblem(followed(), ok)).toBe(null);
	expect(sessionProblem(copied(), { status: 'deleted' })).toBe(null);
	const problem = sessionProblem(followed(), { status: 'deleted' })!;
	expect(problem.title).toBe('Session was deleted');
	expect(problem.message).toBe('This step uses the last settings it saw. Pick another session or keep a copy.');
	expect(sessionProblem(followed(), { status: 'mismatch' })!.title).toBe('Session belongs to another preset');
});

test('conversions offered per mode', () => {
	expect(conversions(followed())).toStrictEqual(['copy']);
	expect(conversions(copied())).toStrictEqual(['update', 'follow', 'save']);
	expect(conversions(copied({ session_source: { name: 'old', id: null } }))).toStrictEqual(['save']);
	expect(conversions(own())).toStrictEqual(['save']);
	expect(conversions(own({ overrides: {} }))).toStrictEqual([]);
	expect((conversions as (...args: unknown[]) => string[])(followed(), { status: 'deleted' })).toStrictEqual(['copy']);
});

test('the session row shows the followed or copied session, and nothing for New', () => {
	expect(sessionRow(followed(), ok)).toStrictEqual({ name: 'Bottle orbit', version: 4 });
	expect(sessionRow(followed())).toStrictEqual({ name: 'Bottle orbit', version: 3 });
	expect(sessionRow(copied())).toStrictEqual({ name: 'Gold restyle', version: 3 });
	expect(sessionRow(copied({ session_source: { id: 's2', name: 'Gold restyle', unversioned: true, version: null } }))).toStrictEqual({ name: 'Gold restyle', version: null });
	expect(sessionRow(own())).toBe(null);
});

test('templates use the followed session, the copy with its edits, or the own settings', () => {
	expect(templateSource(followed(), ok)).toStrictEqual({ session: ok.settings, overrides: {}, hash: 'h-new' });
	expect(templateSource(copied())).toStrictEqual({ session: copied().session_snapshot, overrides: copied().overrides });
	expect(templateSource(own())).toStrictEqual({ session: {}, overrides: own().overrides });
	expect(templateSource(copied({ session_snapshot: null }))).toBe(null);
	expect(templateSource({ ...own(), preset_id: null })).toBe(null);
	expect((templateSource(followed(), { status: 'deleted' }) as { error: string }).error).toMatch(/deleted/);
	expect((templateSource(followed(), undefined) as { error: string }).error).toMatch(/could not be read/);
});

test('session hashes are sent for every followed node that is readable', () => {
	const definition = { nodes: [followed(), copied(), own(), followed({ id: 'n4' })] };
	expect(sessionHashes(definition, { n1: ok, n4: { status: 'deleted' } })).toStrictEqual({ n1: 'h-new' });
	expect(sessionHashes(null, {})).toStrictEqual({});
});

test('effective settings merge the copy with its edits, or come from the followed session', () => {
	expect(effectiveSettings(copied())).toStrictEqual({ prompt: 'gold', formData: { steps: 12, guidance: 3.5 } });
	expect(effectiveSettings(own())).toStrictEqual({ prompt: 'a bottle', formData: { steps: 8 } });
	expect(effectiveSettings(followed(), ok)).toStrictEqual(ok.settings);
	expect(effectiveSettings(followed(), { status: 'deleted' })).toStrictEqual(followed().session_snapshot);
	expect(effectiveSettings(own({ overrides: {} }))).toBe(null);
	expect(effectiveSettings({ kind: 'llm' })).toBe(null);
});

test('stale text says which session changed', () => {
	const labelOf = (id: string) => (({ orbit: 'Orbit left' }) as Record<string, string>)[id] ?? id;
	expect(staleReasonText(null, 'up', labelOf)).toBe(null);
	expect(staleReasonText({ stale: false }, 'up', labelOf)).toBe(null);
	expect(staleReasonText({ stale: true, stale_reason: 'upstream' }, 'up', labelOf)).toBe('An earlier step has newer outputs.');
	expect(staleReasonText({ stale: true }, 'up', labelOf)).toBe('An earlier step has newer outputs.');
	expect(staleReasonText({ stale: true, stale_reason: 'session_changed' }, 'orbit', labelOf)).toBe('Session changed');
	expect(staleReasonText({ stale: true, stale_reason: 'session_changed', stale_node_id: 'orbit' }, 'orbit', labelOf)).toBe('Session changed');
	expect(staleReasonText({ stale: true, stale_reason: 'session_changed', stale_node_id: 'orbit' }, 'up', labelOf)).toBe('Orbit left changed');
});

test('a saved session gets a name no other session of the preset has', () => {
	expect(uniqueSessionName('Gold restyle', [])).toBe('Gold restyle');
	expect(uniqueSessionName('Gold restyle', ['Gold restyle'])).toBe('Gold restyle 2');
	expect(uniqueSessionName('Gold restyle', ['Gold restyle', 'gold restyle 2'])).toBe('Gold restyle 3');
	expect(uniqueSessionName('  ', [])).toBe('Elixir step');
});
