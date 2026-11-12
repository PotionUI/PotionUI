<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { copyText } from '$lib/utils/clipboard';
	import { toasts } from '$lib/stores/toast';
	import { resolveResourceMarkers, type PromptResourceSpec } from '$lib/utils/promptResources';
	import { EMPTY_RESOURCE_NUMBERING, RESOURCE_NUMBERING_CONTEXT_KEY, type ResourceNumbering } from '$lib/utils/resourceNumbering';
	import { resolvePreviewVariables, substituteVariables, type VariableNote } from '$lib/utils/resolvedVariables';
	import { previewConditions } from '$lib/utils/conditionPreview';
	import type { VariablesMap } from '$lib/utils/variableDefs';
	import { buildSyntaxSegments, syntaxColorStyle, syntaxToneClasses, type PromptSyntaxSpec } from '$lib/utils/promptSyntax';
	import { fixedPromptPieces, type FixedPromptParts } from '$lib/utils/presetFixedPrompt';
	import { AdminOnlyMark } from '$lib/components/ui';
	import { expandKitCitations } from '$lib/kits/kitResolve';
	import { kitDirectory } from '$lib/stores/kits';
	import { api } from '$lib/services/api/index';
	import { buildResolveRequest, createKitResolver, hasKitCites, skipLines } from '$lib/kits/kitResolver';
	import { kitFieldsOf, defaultKitField } from '$lib/kits/promptKits';
	import { onDestroy } from 'svelte';

	export let prompt = '';
	export let negativePrompt = '';
	export let promptResources: PromptResourceSpec[] = [];
	export let resourceFieldValues: Record<string, unknown> = {};
	export let promptSyntax: PromptSyntaxSpec[] = [];
	export let variables: VariablesMap | undefined = undefined;
	export let compilePrompt: ((resolved: string) => string) | null = null;
	export let fixedPrompt: FixedPromptParts | null = null;
	export let presetId: string | null = null;
	export let mode: string | null = null;

	const resourceNumbering =
		getContext<Readable<ResourceNumbering | null> | undefined>(RESOURCE_NUMBERING_CONTEXT_KEY) ?? EMPTY_RESOURCE_NUMBERING;

	let open = false;
	let active: 'prompt' | 'negative' = 'prompt';
	let copied = false;

	$: previewVariables = resolvePreviewVariables(variables);
	$: promptConditions = previewConditions(prompt, variables);
	$: negativeConditions = previewConditions(negativePrompt, variables);
	const kitResolver = createKitResolver((request) => api.resolveKitCitations(request));
	onDestroy(() => kitResolver.cancel());

	$: kitFields = kitFieldsOf(promptResources, resourceFieldValues);
	$: promptLocal = resolveResourceMarkers(promptConditions.text, promptResources, resourceFieldValues, $resourceNumbering);
	$: kitCited = hasKitCites([promptLocal]);
	$: kitResolver.request(
		kitCited
			? buildResolveRequest({
					texts: [promptLocal],
					loaded: kitFields.flatMap((entry) => entry.rows.map((row) => row.kit)),
					field: defaultKitField(kitFields),
					presetId,
					mode,
					formData: resourceFieldValues
				})
			: null
	);
	$: serverText = kitCited && $kitResolver.result ? $kitResolver.result.text : null;
	$: kitSkips = kitCited && $kitResolver.result ? skipLines($kitResolver.result.skipped) : [];
	$: resolvedBase = substituteVariables(
		serverText ?? resolveResourceMarkers(expandKitCitations(promptConditions.text, promptResources, resourceFieldValues, $kitDirectory), promptResources, resourceFieldValues, $resourceNumbering),
		previewVariables.replacements
	);
	$: resolvedPrompt = compilePrompt ? compilePrompt(resolvedBase) : resolvedBase;
	$: resolvedNegativePrompt = substituteVariables(
		resolveResourceMarkers(
			expandKitCitations(negativeConditions.text, promptResources, resourceFieldValues, $kitDirectory),
			promptResources,
			resourceFieldValues,
			$resourceNumbering
		),
		previewVariables.replacements
	);

	function mergeNotes(base: VariableNote[], extra: VariableNote[]): VariableNote[] {
		const merged = base.map((note) => {
			const follow = extra.find((e) => e.name === note.name);
			return follow ? { ...note, phrase: `${note.phrase} \u2014 the condition follows it` } : note;
		});
		return [...merged, ...extra.filter((e) => !base.some((n) => n.name === e.name))];
	}

	$: activeConditions = active === 'prompt' ? promptConditions : negativeConditions;
	$: varyingNotes = mergeNotes(
		previewVariables.notes.filter((note) =>
			(active === 'prompt' ? prompt : negativePrompt).includes('${' + note.name + '}')
		),
		activeConditions.notes
	);
	$: rowNotes = mergeNotes(
		previewVariables.notes.filter((note) => prompt.includes('${' + note.name + '}')),
		promptConditions.notes
	);
	$: rowNoteText =
		rowNotes.length === 0
			? ''
			: rowNotes.length === 1
				? `${rowNotes[0].name} ${rowNotes[0].phrase}`
				: `${rowNotes[0].name} ${rowNotes[0].phrase} +${rowNotes.length - 1} more`;
	$: activeText = active === 'prompt' ? resolvedPrompt : resolvedNegativePrompt;
	$: wordCount = activeText.trim() ? activeText.trim().split(/\s+/).length : 0;
	$: activeSegments = buildSyntaxSegments(activeText, promptSyntax);
	$: activePieces = fixedPromptPieces(activeText, fixedPrompt, active);
	$: hasFixedPieces = activePieces.some((piece) => piece.fixed);

	async function copyActive() {
		if (!activeText) return;
		const ok = await copyText(activeText);
		if (ok) {
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} else {
			toasts.error('Could not copy');
		}
	}
</script>

<div class="overflow-hidden rounded-lg border border-line bg-surface-1">
	<div class="flex items-center">
		<button
			type="button"
			class="flex min-w-0 flex-1 items-center gap-2 px-3 py-2.5 text-left hover:bg-surface-2"
			on:click={() => (open = !open)}
			aria-expanded={open}
		>
			<Icon name="document" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
			<span class="min-w-0 flex-1">
				<span class="block text-xs font-medium text-fg">Resolved prompt</span>
				<span class="block truncate font-mono text-2xs text-fg-subtle">
					{resolvedPrompt || 'No enabled prompt content'}
				</span>
				{#if rowNoteText}
					<span class="block truncate text-xs text-fg-muted" data-testid="resolved-row-note">{rowNoteText}</span>
				{/if}
			</span>
			<Icon name="chevron-down" className="h-4 w-4 flex-shrink-0 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}" />
		</button>
		{#if open && activeText}
			<Tooltip text={copied ? 'Copied' : 'Copy resolved prompt'} position="top">
				<button type="button" class="mr-2 inline-flex h-8 w-8 items-center justify-center rounded text-fg-muted hover:bg-surface-2 hover:text-fg" on:click={copyActive} aria-label="Copy resolved prompt">
					<Icon name={copied ? 'check' : 'copy'} className="h-4 w-4 {copied ? 'text-success' : ''}" />
				</button>
			</Tooltip>
		{/if}
	</div>

	{#if open}
		<div class="border-t border-line p-3">
			<div class="mb-3 inline-flex rounded bg-surface-2 p-0.5" role="tablist" aria-label="Resolved prompt type">
				<button type="button" role="tab" aria-selected={active === 'prompt'} class="rounded px-2.5 py-1 text-xs {active === 'prompt' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:text-fg'}" on:click={() => (active = 'prompt')}>Prompt</button>
				<button type="button" role="tab" aria-selected={active === 'negative'} class="rounded px-2.5 py-1 text-xs {active === 'negative' ? 'bg-danger/10 text-danger' : 'text-fg-muted hover:text-fg'}" on:click={() => (active = 'negative')}>Negative</button>
			</div>
			<div class="whitespace-pre-wrap rounded bg-surface-2 p-3 text-sm leading-relaxed text-fg-muted">
				{#if activeText || hasFixedPieces}
					{#each activePieces as piece}{#if piece.fixed}<span class="text-fg-subtle italic" data-fixed-prompt-piece>{piece.text}</span>{:else}{#each activeSegments as segment}
						{#if segment.match}
							{#if segment.match.spec.help}
								<Tooltip text={segment.match.spec.help} wrapperClass="inline">
									<span
										class="rounded px-0.5 {segment.match.color ? '' : syntaxToneClasses(segment.match.tone)}"
										style={segment.match.color ? syntaxColorStyle(segment.match.color) : undefined}
									>{segment.text}</span>
								</Tooltip>
							{:else}
								<span
									class="rounded px-0.5 {segment.match.color ? '' : syntaxToneClasses(segment.match.tone)}"
									style={segment.match.color ? syntaxColorStyle(segment.match.color) : undefined}
								>{segment.text}</span>
							{/if}
						{:else}
							{segment.text}
						{/if}
					{/each}{/if}{/each}
				{:else}
					No {active === 'negative' ? 'negative ' : ''}prompt content
				{/if}
			</div>
			{#if hasFixedPieces}
				<p class="mt-2 flex items-center gap-1.5 text-xs text-fg-subtle" data-fixed-prompt-note>
					<AdminOnlyMark />
					Muted text is added by this preset. Only admins see it.
				</p>
			{/if}
			{#if activeConditions.rows.length > 0}
				<div class="mt-2 overflow-x-auto rounded border border-line" data-testid="condition-preview-table">
					<table class="w-full text-left text-xs">
						<thead class="bg-surface-2 font-mono uppercase tracking-[0.04em] text-fg-subtle">
							<tr>
								<th class="px-2 py-1.5 font-normal">Image</th>
								<th class="px-2 py-1.5 font-normal">Rolled</th>
								<th class="px-2 py-1.5 font-normal">Branch</th>
								<th class="px-2 py-1.5 font-normal">Resolved text</th>
							</tr>
						</thead>
						<tbody>
							{#each activeConditions.rows as row (row.label)}
								<tr class="border-t border-line align-top" data-testid="condition-preview-row">
									<td class="whitespace-nowrap px-2 py-1.5 font-medium text-fg">{row.label}</td>
									<td class="px-2 py-1.5 font-mono tabular-nums text-fg-muted">
										{#each row.rolled as rolled (rolled.name)}
											<span class="mr-1 inline-block rounded border border-line bg-surface-2 px-1.5">${rolled.name} {rolled.value}</span>
										{/each}
									</td>
									<td class="whitespace-nowrap px-2 py-1.5">
										<span class="rounded border px-1.5 py-0.5 font-mono {row.branch === 'OTHERWISE' || row.branch === 'NONE' ? 'border-line bg-surface-2 text-fg-muted' : 'border-signal/40 bg-signal/15 text-signal'}">{row.branch === 'NONE' ? 'NOTHING' : row.branch}</span>
									</td>
									<td class="px-2 py-1.5 text-fg-muted">
										{#each row.pieces as piece}{#if piece.hit}<span class="rounded bg-signal/15 px-0.5 text-fg">{piece.text}</span>{:else}{piece.text}{/if}{/each}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
			{#if varyingNotes.length > 0}
				<ul class="mt-2 space-y-0.5 text-xs text-fg-subtle" aria-label="Variables that vary">
					{#each varyingNotes as note (note.name)}
						<li><span class="font-mono text-fg-muted">{note.name}</span> {note.phrase}</li>
					{/each}
				</ul>
			{/if}
			<p class="mt-2 font-mono text-2xs tabular-nums text-fg-subtle">{wordCount} words · {activeText.length} characters</p>
		</div>
	{/if}
</div>
