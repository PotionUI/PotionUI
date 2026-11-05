import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot, fulfillJson, mockAdminSocket } from './helpers';

const JOURNEY = 'admin-models-auto-organize';
const API = '/api/admin/organize/instance';
const AUTO_URL = '/admin?tab=models&view=auto-organize';

type Obj = Record<string, any>;

const TEXT_OPERATORS = ['contains', 'not_contains', 'starts_with', 'ends_with', 'is', 'is_not'];

function fact(key: string, label: string, kind: string, operators: string[], extra: Obj = {}): Obj {
	return {
		key,
		label,
		subjects: ['model'],
		kind,
		operators,
		picker: {},
		options: null,
		has_options_endpoint: false,
		description: '',
		source: 'core',
		component: null,
		previewable: true,
		...extra
	};
}

function action(key: string, label: string, schema: Obj[], extra: Obj = {}): Obj {
	return {
		key,
		label,
		subjects: ['model'],
		config_schema: schema,
		requires_admin: false,
		undoable: true,
		source: 'core',
		component: null,
		description: '',
		scope: 'instance',
		notes: [],
		needs: [],
		done_label: '',
		paced: false,
		...extra
	};
}

const CATALOG: Obj = {
	scope: 'instance',
	subjects: [
		{ key: 'model', label: 'Models', trigger: 'model_added', trigger_label: 'When a model is added', collection_scope: 'models', supports_tags: false }
	],
	kinds: {
		text: { operators: TEXT_OPERATORS },
		enum: { operators: ['is', 'is_any_of', 'is_not'] }
	},
	operators: {
		is: 'is',
		is_not: 'is not',
		is_any_of: 'is any of',
		contains: 'contains',
		not_contains: 'does not contain',
		starts_with: 'starts with',
		ends_with: 'ends with'
	},
	facts: [
		fact('name', 'Name', 'text', TEXT_OPERATORS, { picker: { placeholder: 'part of the name' }, ignore_separators: true }),
		fact('model_type', 'Model type', 'enum', ['is', 'is_any_of', 'is_not'], {
			picker: { multi: true },
			options: [
				{ value: 'checkpoint', label: 'Checkpoint' },
				{ value: 'lora', label: 'LoRA' }
			]
		}),
		fact('source', 'Source', 'enum', ['is', 'is_any_of', 'is_not'], {
			picker: { multi: true },
			options: [
				{ value: 'local', label: 'Local' },
				{ value: 'civitai', label: 'CivitAI' }
			]
		})
	],
	actions: [
		action('assign_model_group', 'Give a user group access', [{ key: 'group_id', kind: 'user_group', label: 'User group', required: true }], {
			description: 'Adds the model to a user group, so every member sees and can use it.',
			done_label: 'Gave a group access'
		}),
		action('add_model_tags', 'Add model tags', [{ key: 'tags', kind: 'tag_list', label: 'Tags', required: true }], {
			description: 'Adds tags to the model. Model tags are shared by everyone.'
		}),
		action(
			'set_model_type',
			'Set model type',
			[
				{
					key: 'model_type',
					kind: 'enum',
					label: 'Model type',
					required: true,
					options: [
						{ value: 'checkpoint', label: 'checkpoint' },
						{ value: 'lora', label: 'lora' },
						{ value: 'vae', label: 'vae' }
					]
				}
			],
			{ description: 'Sets the type of models that have none.', needs: ['digest'] }
		),
		action('civitai-provider.fetch_info', 'Fetch CivitAI data', [], {
			undoable: false,
			source: 'civitai-provider',
			description: 'Looks the model up on CivitAI by its file hash and fills in its empty description, trigger words, tags and previews.',
			notes: [
				'Only an exact SHA-256 match is accepted, so a wrong model is never filled in.',
				'A model that is not on CivitAI is skipped, one that could not be reached is tried again.'
			],
			needs: ['digest'],
			done_label: 'Fetched CivitAI data',
			paced: true
		})
	]
};

const GROUPS = [
	{ id: 'g-krea', name: 'krea-2', member_count: 6, model_count: 14 },
	{ id: 'g-flux', name: 'flux-team', member_count: 3, model_count: 31 }
];

function rule(id: string, name: string, over: Obj = {}): Obj {
	return {
		id,
		name,
		subject: 'model',
		trigger: 'model_added',
		match: 'all',
		conditions: [{ fact: 'name', operator: 'contains', value: 'krea2', ignore_separators: true }],
		actions: [{ action: 'assign_model_group', config: { group_id: 'g-krea' } }],
		enabled: true,
		stop_after: false,
		position: 0,
		status: 'active',
		paused_reason: null,
		paused_at: null,
		issues: [],
		targets: { collections: [], groups: [{ id: 'g-krea', name: 'krea-2', exists: true }] },
		filed_count: 14,
		waiting_count: 0,
		skipped_count: 0,
		last_run_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
		created_at: new Date(Date.now() - 9 * 86400_000).toISOString(),
		updated_at: new Date(Date.now() - 6 * 86400_000).toISOString(),
		scope: 'instance',
		created_by: 'u-mira',
		created_by_name: 'mira',
		updated_by: 'u-mira',
		updated_by_name: 'mira',
		...over
	};
}

function seedRules(): Obj[] {
	return [
		rule('r-krea', 'Krea-2 models to krea-2'),
		rule('r-civ', 'Fetch CivitAI data', {
			position: 1,
			conditions: [
				{ fact: 'source', operator: 'is_not', value: 'civitai' },
				{ fact: 'model_type', operator: 'is_any_of', value: ['checkpoint', 'lora'] }
			],
			actions: [{ action: 'civitai-provider.fetch_info', config: {} }],
			targets: { collections: [], groups: [] },
			filed_count: 19,
			waiting_count: 9,
			skipped_count: 3,
			updated_by_name: 'ola'
		}),
		rule('r-ill', 'Illustrious to illustrious', {
			position: 2,
			enabled: false,
			status: 'paused',
			paused_reason: 'group_missing',
			conditions: [{ fact: 'name', operator: 'contains', value: 'illustrious' }],
			actions: [{ action: 'assign_model_group', config: { group_id: 'gone' } }],
			targets: { collections: [], groups: [{ id: 'gone', name: '', exists: false }] },
			filed_count: 6
		})
	];
}

function civitaiRun(): Obj {
	return {
		id: 'run-civ',
		rule_id: 'r-civ',
		rule_name: 'Fetch CivitAI data',
		rule_deleted: false,
		subject: 'model',
		kind: 'backfill',
		status: 'completed',
		started_at: new Date(Date.now() - 40 * 60_000).toISOString(),
		finished_at: new Date(Date.now() - 38 * 60_000).toISOString(),
		matched: 31,
		applied: 19,
		undone: 0,
		can_undo: false,
		scope: 'instance',
		started_by: 'u-ola',
		started_by_name: 'ola',
		outcomes: { updated: 19, skipped: 3, waiting: 9 },
		reasons: [
			{ outcome: 'waiting', code: 'unreachable', detail: 'CivitAI could not be reached', count: 9 },
			{ outcome: 'skipped', code: 'hash_mismatch', detail: 'CivitAI answered with a different file (hash mismatch), so the answer was ignored', count: 3 }
		],
		changes: {
			collections: [],
			tags: [],
			groups: [],
			other: [{ action: 'civitai-provider.fetch_info', label: 'Fetch CivitAI data', done_label: 'Fetched CivitAI data', count: 19, live: 19 }]
		}
	};
}

function groupRun(): Obj {
	return {
		id: 'run-grp',
		rule_id: 'r-krea',
		rule_name: 'Krea-2 models to krea-2',
		rule_deleted: false,
		subject: 'model',
		kind: 'backfill',
		status: 'completed',
		started_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
		finished_at: new Date(Date.now() - 3 * 3600_000 + 5000).toISOString(),
		matched: 14,
		applied: 14,
		undone: 0,
		can_undo: true,
		scope: 'instance',
		started_by: 'u-mira',
		started_by_name: 'mira',
		outcomes: { updated: 14, skipped: 0, waiting: 0 },
		reasons: [],
		changes: { collections: [], tags: [], groups: [{ id: 'g-krea', name: 'krea-2', count: 14, live: 14 }], other: [] }
	};
}

function runItems(status: string | null, page: number): Obj {
	const make = (name: string, st: string, detail: string, attempts = 0) => ({
		item_type: 'model',
		item_id: `${st}-${name}`,
		name,
		model_type: 'lora',
		status: st,
		code: st === 'waiting' ? 'unreachable' : st === 'skipped' ? 'hash_mismatch' : null,
		detail,
		attempts,
		next_try_at: null
	});
	const all = [
		make('style_pack_xl', 'updated', 'CivitAI'),
		make('mystery_lora_v2', 'skipped', 'CivitAI answered with a different file (hash mismatch), so the answer was ignored'),
		make('detail_slider_xl', 'waiting', 'CivitAI could not be reached', 2)
	];
	const rows = status ? all.filter((r) => r.status === status) : all;
	const totals: Obj = { updated: 19, skipped: 3, waiting: 9 };
	return { run: civitaiRun(), items: rows, total: status ? totals[status] : 31, page, page_size: 25 };
}

function matches(page: number, status: string | null): Obj {
	const total = status === 'already_done' ? 5 : 60;
	const size = Math.min(25, Math.max(0, total - (page - 1) * 25));
	return {
		total,
		page,
		page_size: 25,
		approximate: false,
		counts: { would_change: 55, already_done: 5 },
		items: Array.from({ length: size }, (_, i) => ({
			item_type: 'model',
			item_id: `m-${page}-${i}`,
			name: `krea2_family_${page}_${i}`,
			model_type: 'checkpoint',
			status: status === 'already_done' ? 'already_done' : 'would_change'
		}))
	};
}

interface Log {
	created: Obj[];
	retries: number;
	undone: string[];
	applied: string[];
	matchRequests: Obj[];
}

interface Options {
	rules?: Obj[];
	runs?: Obj[];
	retry?: 'ok' | 'nothing_waiting';
	preview?: Obj;
	failRules?: boolean;
}

async function mockApi(page: Page, options: Options = {}): Promise<Log> {
	const log: Log = { created: [], retries: 0, undone: [], applied: [], matchRequests: [] };
	let rules = options.rules ?? seedRules();
	const runs = options.runs ?? [civitaiRun(), groupRun()];
	let jobPolls = 0;

	await page.route(/\/api\/admin\/organize\/instance\/.*/, async (route: Route) => {
		const request = route.request();
		const method = request.method();
		const path = new URL(request.url()).pathname.slice(API.length);
		const ok = (data: unknown) => fulfillJson(route, { success: true, data });

		if (path === '/catalog') return ok(CATALOG);
		if (path === '/rules' && method === 'GET') {
			return options.failRules ? fulfillJson(route, { detail: { error: 'organize_list_failed', message: 'Nope' } }, 500) : ok(rules);
		}
		if (path === '/rules' && method === 'POST') {
			const body = request.postDataJSON();
			log.created.push(body);
			const created = rule(`r-new-${log.created.length}`, body.name, {
				...body,
				position: rules.length,
				filed_count: 0,
				last_run_at: null,
				updated_by_name: 'admin',
				targets: { collections: [], groups: GROUPS.filter((g) => body.actions.some((a: Obj) => a.config?.group_id === g.id)).map((g) => ({ id: g.id, name: g.name, exists: true })) }
			});
			rules = [...rules, created];
			return ok(created);
		}
		if (path === '/rules/reorder') return ok(rules);
		const single = path.match(/^\/rules\/([^/]+)$/);
		if (single && method === 'GET') return ok(rules.find((r) => r.id === single[1]) ?? null);
		if (single && method === 'PATCH') return ok({ ...rules.find((r) => r.id === single[1]), ...request.postDataJSON() });
		const applyMatch = path.match(/^\/rules\/([^/]+)\/apply-existing$/);
		if (applyMatch) {
			log.applied.push(applyMatch[1]);
			return ok({ id: 'job-1', rule_id: applyMatch[1], rule_name: 'Fetch CivitAI data', status: 'running', total: 31, processed: 14, applied: 9, skipped: 1, waiting: 4, kind: 'apply', scope: 'instance', run_id: null, error: null, started_at: new Date().toISOString(), finished_at: null });
		}
		if (path === '/jobs/job-1') {
			jobPolls += 1;
			return ok({ id: 'job-1', rule_id: 'r-civ', rule_name: 'Fetch CivitAI data', status: 'running', total: 31, processed: 14 + jobPolls, applied: 9, skipped: 1, waiting: 4, kind: 'apply', scope: 'instance', run_id: null, error: null, started_at: new Date().toISOString(), finished_at: null });
		}
		if (path === '/preview') {
			return ok(
				options.preview ?? {
					matched: 14,
					already_handled: 0,
					would_change: 14,
					approximate: false,
					sample: ['krea2_dev_bf16', 'krea2_dev_fp8', 'krea2_turbo_fp8', 'krea2_edit_fp8', 'krea2_base_bf16'].map((name, i) => ({ item_type: 'model', item_id: `m${i}`, name, model_type: 'checkpoint' })),
					duplicates: []
				}
			);
		}
		if (path === '/preview/matches') {
			const body = request.postDataJSON();
			log.matchRequests.push(body);
			return ok(matches(body.page ?? 1, body.status ?? null));
		}
		if (path === '/activity') return ok({ runs, next_before: null });
		const items = path.match(/^\/runs\/([^/]+)\/items$/);
		if (items) {
			const params = new URL(request.url()).searchParams;
			return ok(runItems(params.get('status'), Number(params.get('page') ?? 1)));
		}
		if (path.match(/^\/runs\/([^/]+)\/retry$/)) {
			log.retries += 1;
			if (options.retry === 'nothing_waiting') {
				return fulfillJson(route, { detail: { error: 'nothing_waiting', message: 'Nothing is waiting in this run' } }, 409);
			}
			return ok({ id: 'job-2', rule_id: 'r-civ', rule_name: 'Fetch CivitAI data', status: 'queued', total: 9, processed: 0, applied: 0, kind: 'retry', scope: 'instance', run_id: null, error: null, started_at: new Date().toISOString(), finished_at: null });
		}
		const undo = path.match(/^\/runs\/([^/]+)\/undo$/);
		if (undo) {
			log.undone.push(undo[1]);
			return ok({ undone: 14, items: 14, skipped: 0, run: { ...groupRun(), can_undo: false, status: 'undone', undone: 14 } });
		}
		if (path.startsWith('/facts/')) return ok([]);
		return ok(null);
	});
	await page.route('**/api/user-groups', (route) => fulfillJson(route, { success: true, data: GROUPS }));
	await mockAdminSocket(page, () => ({ state: 'idle' }));
	return log;
}

const isPhone = (page: Page) => (page.viewportSize()?.width ?? 1440) < 768;

async function openAutoOrganize(page: Page, search = '') {
	await page.goto(`${AUTO_URL}${search}`);
	await expect(page.getByTestId('model-auto-organize').or(page.getByLabel('Rule name')).first()).toBeVisible({ timeout: 20000 });
}

async function expectNoHorizontalOverflow(page: Page) {
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
	expect(overflow).toBeLessThanOrEqual(0);
}

function conditionRow(page: Page) {
	return page.locator('[data-testid="condition-row"]').first();
}

function actionRow(page: Page, key: string) {
	return page.locator(`[data-testid="action-row"][data-action="${key}"]`);
}

for (const viewport of [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
]) {
	test.describe(`admin models auto-organize - ${viewport.name}`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		test('Auto-organize is the last section of the Models sidebar and lists the instance rules', async ({ page }) => {
			await loginAsOwner(page);
			await mockApi(page);
			await page.goto('/admin?tab=models');
			if (isPhone(page)) {
				const select = page.locator('select').filter({ has: page.locator('option', { hasText: 'Auto-organize' }) });
				await expect(select.locator('option')).toHaveText(['All models', 'Attributes', 'Folders', 'Auto-organize']);
				await select.selectOption({ label: 'Auto-organize' });
			} else {
				const entries = page.getByRole('listbox', { name: 'Models sections' }).getByRole('option');
				await expect(entries.last()).toContainText('Auto-organize');
				await entries.last().click();
			}
			await expect(page).toHaveURL(/view=auto-organize/);
			await expect(page.getByTestId('rule-card')).toHaveCount(3, { timeout: 15000 });
			await expect(page.getByText('These rules belong to the instance')).toBeVisible();

			const krea = page.locator('[data-rule-id="r-krea"]');
			await expect(krea).toContainText('Name contains krea2 (ignoring dashes and underscores)');
			await expect(krea).toContainText('give krea-2 access');
			await expect(krea.getByTestId('rule-changed-by')).toContainText('Changed by mira');

			const civ = page.locator('[data-rule-id="r-civ"]');
			await expect(civ.getByTestId('rule-waiting')).toContainText('9 waiting');
			await expect(civ.getByTestId('rule-skipped')).toContainText('3 skipped');
			await expect(civ.getByTestId('rule-changed-by')).toContainText('Changed by ola');
			await expect(civ).toContainText('fetch CivitAI data');

			const paused = page.locator('[data-rule-id="r-ill"]');
			await expect(paused).toContainText('Its user group is gone, so it was paused');
			await expect(page.getByTestId('rule-template')).toHaveCount(2);
			await expectNoHorizontalOverflow(page);
			await screenshot(page, JOURNEY, `${viewport.name}-01-rules`);
		});

		test('example one: name contains krea2 (ignore separators) gives the krea-2 group access', async ({ page }) => {
			await loginAsOwner(page);
			const log = await mockApi(page, { rules: [] });
			await openAutoOrganize(page);
			await expect(page.getByText('No rules yet')).toBeVisible();
			await screenshot(page, JOURNEY, `${viewport.name}-02-empty`);
			await page.getByRole('button', { name: 'New rule' }).first().click();

			await page.getByLabel('Rule name').fill('Krea-2 models to krea-2');
			await page.getByRole('button', { name: 'Add condition' }).click();
			await expect(conditionRow(page)).toBeVisible();
			await conditionRow(page).locator('input[aria-label="Name"]').fill('krea2');
			const separators = page.getByTestId('ignore-separators');
			await expect(separators).toContainText('Ignore dashes and underscores');
			await separators.locator('input').check();

			const group = actionRow(page, 'assign_model_group');
			await expect(group).toBeVisible();
			await group.locator('[aria-haspopup="listbox"]').click();
			await page.getByRole('option', { name: 'krea-2 · 6 members' }).click();

			await expect(page.getByTestId('preview-matched')).toContainText('14', { timeout: 15000 });
			await expect(page.getByTestId('preview-sample').locator('li')).toHaveCount(5);
			await expect(page.getByText('Also run it on the 14 existing models when I save')).toBeVisible();
			await expectNoHorizontalOverflow(page);
			await screenshot(page, JOURNEY, `${viewport.name}-03-editor-krea`);

			await page.getByRole('button', { name: 'Create', exact: true }).click();
			await expect.poll(() => log.created.length).toBe(1);
			expect(log.created[0]).toEqual({
				name: 'Krea-2 models to krea-2',
				subject: 'model',
				match: 'all',
				conditions: [{ fact: 'name', operator: 'contains', value: 'krea2', ignore_separators: true }],
				actions: [{ action: 'assign_model_group', config: { group_id: 'g-krea' } }],
				enabled: true,
				stop_after: false
			});
		});

		test('all matches page with the numeric pagination and filter by what would change', async ({ page }) => {
			await loginAsOwner(page);
			const log = await mockApi(page);
			await openAutoOrganize(page, '&rule=r-krea');
			await page.getByRole('button', { name: 'Show all 14' }).click();
			const dialog = page.getByTestId('matches-dialog');
			await expect(dialog.getByTestId('match-row')).toHaveCount(25);
			await expect(dialog).toContainText('Showing 1-25 of 60');
			await screenshot(page, JOURNEY, `${viewport.name}-04-matches`);
			await dialog.getByRole('button', { name: 'Page 3' }).click();
			await expect(dialog).toContainText('Showing 51-60 of 60');
			await expect(dialog.getByTestId('match-row')).toHaveCount(10);
			expect(log.matchRequests.at(-1)).toMatchObject({ page: 3, page_size: 25 });
			await dialog.getByRole('button', { name: 'Already done' }).click();
			await expect(dialog).toContainText('Showing 1-5 of 5');
			expect(log.matchRequests.at(-1)).toMatchObject({ status: 'already_done', page: 1 });
		});

		test('example two: on file verified, fetch CivitAI data, with the caveat, from the template', async ({ page }) => {
			await loginAsOwner(page);
			const log = await mockApi(page);
			await openAutoOrganize(page);
			await page.getByTestId('rule-template').filter({ hasText: 'Fetch CivitAI data for new models' }).click();

			await expect(page.getByLabel('Rule name')).toHaveValue('Fetch CivitAI data');
			const row = actionRow(page, 'civitai-provider.fetch_info');
			await expect(row).toBeVisible();
			await expect(row.getByTestId('action-origin')).toHaveText('civitai-provider');
			await expect(row.getByTestId('action-description')).toContainText('Looks the model up on CivitAI');
			await expect(row.getByTestId('action-notes')).toContainText('Only an exact SHA-256 match is accepted');
			await expect(page.getByTestId('rule-trigger-note')).toContainText('Runs once the model file is verified');
			await expect(page.getByTestId('ignore-separators')).toHaveCount(0);
			await expectNoHorizontalOverflow(page);
			await screenshot(page, JOURNEY, `${viewport.name}-05-editor-civitai`);

			await expect(page.getByTestId('preview-matched')).toBeVisible({ timeout: 15000 });
			await page.getByTestId('apply-existing').check();
			await page.getByRole('button', { name: 'Create', exact: true }).click();
			await expect.poll(() => log.created.length).toBe(1);
			expect(log.created[0].actions).toEqual([{ action: 'civitai-provider.fetch_info', config: {} }]);
			await expect.poll(() => log.applied.length).toBe(1);
			const progress = page.getByTestId('job-progress');
			await expect(progress).toContainText('on existing models');
			await expect(progress.getByTestId('job-breakdown')).toContainText('Skipped');
			await expect(progress.getByTestId('job-breakdown')).toContainText('Waiting');
			await screenshot(page, JOURNEY, `${viewport.name}-06-running`);
		});

		test('Activity shows waiting and skipped reasons, Details filters them and the waiting ones are tried again', async ({ page }) => {
			await loginAsOwner(page);
			const log = await mockApi(page);
			await openAutoOrganize(page, '&pane=activity');
			const run = page.locator('[data-run-id="run-civ"]');
			await expect(run).toContainText('9 waiting: CivitAI could not be reached');
			await expect(run).toContainText('3 skipped: CivitAI answered with a different file (hash mismatch)');
			await expect(run).toContainText('by ola');
			await expect(run.getByRole('button', { name: 'Undo' })).toHaveCount(0);
			await expectNoHorizontalOverflow(page);
			await screenshot(page, JOURNEY, `${viewport.name}-07-activity`);

			await run.getByRole('button', { name: 'Details' }).click();
			const dialog = page.getByTestId('run-details');
			await expect(dialog.getByTestId('run-item')).toHaveCount(3);
			await dialog.getByRole('button', { name: 'Skipped 3' }).click();
			const skipped = dialog.locator('[data-outcome="skipped"]');
			await expect(skipped).toHaveCount(1);
			await expect(skipped).toContainText('mystery_lora_v2');
			await expect(skipped).toContainText('hash mismatch');
			await screenshot(page, JOURNEY, `${viewport.name}-08-run-details-skipped`);

			await dialog.getByRole('button', { name: 'Waiting 9' }).click();
			const waiting = dialog.locator('[data-outcome="waiting"]');
			await expect(waiting).toContainText('detail_slider_xl');
			await expect(waiting).toContainText('CivitAI could not be reached');
			await screenshot(page, JOURNEY, `${viewport.name}-09-run-details-waiting`);

			await dialog.getByRole('button', { name: 'Try the 9 waiting again now' }).click();
			await expect(page.getByText('Trying 9 models again')).toBeVisible();
			await expect(dialog).toBeHidden();
			expect(log.retries).toBe(1);
		});

		test('trying the waiting ones again when none are left says so instead of failing', async ({ page }) => {
			await loginAsOwner(page);
			const log = await mockApi(page, { retry: 'nothing_waiting' });
			await openAutoOrganize(page, '&pane=activity');
			await page.locator('[data-run-id="run-civ"]').getByRole('button', { name: 'Details' }).click();
			const dialog = page.getByTestId('run-details');
			await dialog.getByRole('button', { name: 'Try the 9 waiting again now' }).click();
			await expect(page.getByText('Nothing is waiting in this run any more.')).toBeVisible();
			expect(log.retries).toBe(1);
			await expect(dialog.getByRole('button', { name: /waiting again now/ })).toHaveCount(0);
			await expect(dialog).toBeVisible();
		});

		test('undoing a group run asks first and says the group stays', async ({ page }) => {
			await loginAsOwner(page);
			const log = await mockApi(page);
			await openAutoOrganize(page, '&pane=activity');
			const run = page.locator('[data-run-id="run-grp"]');
			await expect(run).toContainText('Gave krea-2 access to 14 models');
			await run.getByRole('button', { name: 'Undo' }).click();
			await expect(page.getByText('the group stays')).toBeVisible();
			await screenshot(page, JOURNEY, `${viewport.name}-10-confirm-undo`);
			await page.getByRole('button', { name: 'Confirm', exact: true }).click();
			await expect.poll(() => log.undone).toEqual(['run-grp']);
		});

		test('a rules failure is shown with a retry', async ({ page }) => {
			await loginAsOwner(page);
			await mockApi(page, { failRules: true });
			await page.goto(AUTO_URL);
			await expect(page.getByText('Nope').or(page.getByText('The rules could not be loaded.')).first()).toBeVisible({ timeout: 20000 });
			await screenshot(page, JOURNEY, `${viewport.name}-11-error`);
		});
	});
}
