<script module>
	const varsCache = new Map<string, Record<string, unknown>>();
</script>

<script lang="ts">
	import { canPrepare, prepareNodeRequest, requestSummary } from '$lib/elixirs/requests';
	import { templateSource } from '$lib/elixirs/follow';
	import type { ElixirsApi } from '$lib/services/api/elixirs';
	import type { ElixirNode, FollowedState } from '$lib/elixirs/types';

	interface Props {
		node: ElixirNode;
		client: ElixirsApi;
		followed?: FollowedState | null;
	}

	let { node, client, followed = undefined }: Props = $props();

	let presetVars = $state<Record<string, unknown> | null>(null);
	let loadFailed = $state(false);

	$effect(() => {
		const presetId = node.preset_id;
		if (!presetId) return;
		if (varsCache.has(presetId)) {
			presetVars = varsCache.get(presetId) ?? null;
			return;
		}
		let cancelled = false;
		client.presetVars(presetId).then((result) => {
			if (cancelled) return;
			if (result.ok) {
				varsCache.set(presetId, result.data);
				presetVars = result.data;
			} else {
				loadFailed = true;
			}
		});
		return () => {
			cancelled = true;
		};
	});

	let prepared = $derived(presetVars ? prepareNodeRequest(node, presetVars, undefined, { followed }) : null);
	let summary = $derived(prepared?.ok ? requestSummary(prepared.request) : null);
	let json = $derived(prepared?.ok ? JSON.stringify(prepared.request, null, 2) : '');
</script>

{#if canPrepare() && templateSource(node, followed)}
	<details class="request-preview rounded border border-line" aria-label="Request preview">
		<summary class="cursor-pointer px-2.5 py-2 text-sm font-medium text-fg-muted">Request preview</summary>
		<div class="flex flex-col gap-2 border-t border-line px-2.5 py-2.5">
		{#if loadFailed}
			<p class="text-sm text-danger">The preset could not be loaded, so the request cannot be previewed.</p>
		{:else if !prepared}
			<p class="text-sm text-fg-subtle">Preparing…</p>
		{:else if !prepared.ok}
			<p class="rounded border border-danger/25 bg-danger/10 px-2.5 py-1.5 text-sm text-danger">{prepared.message}</p>
		{:else if summary}
			<dl class="grid grid-cols-2 gap-2 text-sm">
				<div class="col-span-2">
					<dt class="text-fg-subtle">{summary.prompts.length > 1 ? `Prompts (${summary.prompts.length})` : 'Prompt'}</dt>
					<dd class="line-clamp-3 text-fg">{summary.prompts[0] || 'No prompt (this mode needs none)'}</dd>
				</div>
				<div>
					<dt class="text-fg-subtle">Settings</dt>
					<dd class="font-mono tabular-nums text-fg">{summary.fields}</dd>
				</div>
				<div>
					<dt class="text-fg-subtle">Seed</dt>
					<dd class="font-mono tabular-nums text-fg">{summary.seed === null || summary.seed === -1 ? 'Random' : summary.seed}</dd>
				</div>
				{#if summary.variables > 0}
					<div>
						<dt class="text-fg-subtle">Variables</dt>
						<dd class="font-mono tabular-nums text-fg">{summary.variables}</dd>
					</div>
				{/if}
				{#if summary.director}
					<div>
						<dt class="text-fg-subtle">Video Director</dt>
						<dd class="font-mono text-fg">{summary.director}</dd>
					</div>
				{/if}
			</dl>
			<p class="text-sm text-fg-subtle">Shuffled chips and variables are picked again for every brew.</p>
			<details class="rounded border border-line">
				<summary class="cursor-pointer px-2.5 py-1.5 text-sm text-fg-muted">Full request</summary>
				<pre class="max-h-64 overflow-auto border-t border-line px-2.5 py-2 font-mono text-sm text-fg">{json}</pre>
			</details>
		{/if}
		</div>
	</details>
{/if}
