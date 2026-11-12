<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/services/api/index';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Alert, Button, CopyButton, SegmentedControl, Spinner, Switch } from '$lib/components/ui';
	import { parseApiError } from '$lib/utils/apiErrors';
	import {
		buildExamples,
		changeableFields,
		type ExampleKind,
		type ScriptTemplate
	} from '$lib/session/scriptExamples';

	let {
		sessionId,
		sessionName,
		onClose
	}: { sessionId: string | null; sessionName: string; onClose: () => void } = $props();

	const EXAMPLE_ITEMS: { id: ExampleKind; label: string }[] = [
		{ id: 'curl', label: 'curl' },
		{ id: 'python', label: 'Python' },
		{ id: 'javascript', label: 'JavaScript' },
		{ id: 'mcp', label: 'MCP' }
	];

	let template = $state<ScriptTemplate | null>(null);
	let mode = $state<string | undefined>(undefined);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let hasKey = $state<boolean | null>(null);
	let kind = $state<ExampleKind>('curl');
	let pinned = $state(false);

	let fields = $derived(template ? changeableFields(template) : []);
	let examples = $derived(
		template
			? buildExamples(template, { base: typeof window === 'undefined' ? '' : window.location.origin, pinned })
			: null
	);

	async function load(id: string, selected?: string) {
		loading = true;
		error = null;
		try {
			const response = await api.getSessionScriptTemplate(id, selected);
			if (!response.success || !response.data) throw new Error(response.message || 'Could not load this session.');
			template = response.data;
			mode = response.data.session.mode;
		} catch (err) {
			template = null;
			error = parseApiError(err).message ?? 'Could not load this session.';
		} finally {
			loading = false;
		}
	}

	async function checkKeys() {
		try {
			const response = await api.listApiKeys();
			const keys = response.data?.keys ?? [];
			hasKey = keys.some((key) => key.status === 'active' && key.scopes.includes('generate'));
		} catch {
			hasKey = null;
		}
	}

	$effect(() => {
		if (!sessionId) return;
		template = null;
		pinned = false;
		kind = 'curl';
		void load(sessionId);
		void checkKeys();
	});

	function chooseMode(id: string) {
		if (sessionId && id !== mode) void load(sessionId, id);
	}

	function openKeys() {
		onClose();
		void goto('/settings#keys');
	}
</script>

<BaseModal
	isOpen={sessionId !== null}
	title="Use from a script"
	subtitle={sessionName}
	size="lg"
	on:close={onClose}
>
	{#if loading && !template}
		<div class="flex justify-center py-10"><Spinner /></div>
	{:else if error}
		<Alert variant="danger">{error}</Alert>
	{:else if template}
		<div class="flex flex-col gap-4">
			<div class="flex flex-wrap items-center gap-2">
				<span class="text-xs text-fg-muted">Session id</span>
				<code class="font-mono text-xs text-fg tabular-nums">{template.session.id}</code>
				<CopyButton text={template.session.id} ariaLabel="Copy session id" size="xs" />
			</div>

			{#if template.session.modes.length > 1}
				<SegmentedControl
					variant="toggle"
					ariaLabel="Mode"
					items={template.session.modes.map((id) => ({ id, label: id }))}
					selected={mode ?? template.session.mode}
					onSelect={chooseMode}
				/>
			{/if}

			{#if template.editor_only}
				<Alert variant="warning">{template.editor_only.message}</Alert>
			{:else}
				{#each template.problems as problem (problem.code + problem.message)}
					<Alert variant="warning" density="compact">{problem.message}</Alert>
				{/each}

				<section>
					<h3 class="mb-1 text-sm font-semibold text-fg">What a script can change</h3>
					<p class="mb-2 text-xs text-fg-muted">
						Everything else comes from the session. Send these under <code class="font-mono">overrides</code>.
					</p>
					<ul class="divide-y divide-line rounded border border-line text-xs">
						<li class="flex items-center gap-2 px-2.5 py-1.5">
							<span class="w-40 font-mono text-fg">prompt</span>
							<span class="text-fg-muted">text</span>
							<span class="ml-auto min-w-0 truncate text-fg-muted">{template.prompt.positive}</span>
						</li>
						{#each fields as field (field.name)}
							<li class="flex items-center gap-2 px-2.5 py-1.5">
								<span class="w-40 truncate font-mono text-fg">form.{field.name}</span>
								<span class="text-fg-muted">{field.type}</span>
								{#if field.locked}
									<span class="ml-auto text-fg-subtle">locked by an administrator</span>
								{:else if field.value !== undefined && field.value !== null}
									<span class="ml-auto min-w-0 truncate font-mono tabular-nums text-fg-muted">{String(field.value)}</span>
								{/if}
							</li>
						{/each}
					</ul>
				</section>

				{#if hasKey === false}
					<Alert variant="info" icon>
						You have no active API key that can generate. Create one with the Read and Generate scopes.
						{#snippet actions()}
							<Button variant="secondary" size="sm" onclick={openKeys}>Create a key</Button>
						{/snippet}
					</Alert>
				{/if}

				{#if examples}
					<section class="flex flex-col gap-2">
						<div class="flex flex-wrap items-center justify-between gap-2">
							<SegmentedControl
								variant="toggle"
								ariaLabel="Example language"
								items={EXAMPLE_ITEMS}
								selected={kind}
								onSelect={(id) => (kind = id as ExampleKind)}
							/>
							<label class="flex items-center gap-2 text-xs text-fg-muted">
								<Switch bind:checked={pinned} size="sm" label="Pin these settings" />
								Pin these settings
							</label>
						</div>
						<div class="relative rounded border border-line bg-surface-2">
							<div class="absolute right-1.5 top-1.5">
								<CopyButton text={examples[kind]} ariaLabel="Copy example" size="xs" />
							</div>
							<pre class="overflow-x-auto p-3 pr-10 font-mono text-xs text-fg">{examples[kind]}</pre>
						</div>
						<p class="text-xs text-fg-muted">
							The key is read from <code class="font-mono">$POTIONUI_KEY</code>; never paste a real key into a script you share.
							{#if pinned}A changed session then answers 409 instead of generating.{/if}
						</p>
					</section>
				{/if}
			{/if}
		</div>
	{/if}
	<div slot="footer" class="flex justify-between">
		<Button variant="ghost" size="sm" onclick={openKeys}>API keys settings</Button>
		<Button variant="secondary" size="sm" onclick={onClose}>Close</Button>
	</div>
</BaseModal>
