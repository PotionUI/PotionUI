<script lang="ts">
	import { onDestroy } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/services/api/index';
	import { skippedNotes, type KitCite } from '$lib/kits/kitChips';
	import { parseKitKey } from '$lib/kits/kitMarker';
	import { buildResolveRequest, createKitResolver, skipLines } from '$lib/kits/kitResolver';
	import { defaultKitField, kitCapableFields, kitFieldsOf, type PromptSpecLike } from '$lib/kits/promptKits';
	import { resourceMarkerRegex } from '$lib/utils/promptResources';
	import { ensureKits, kitDirectory } from '$lib/stores/kits';
	import { kitNotices } from '$lib/stores/kitNotices';

	let {
		texts,
		specs,
		formData = null,
		presetId = null,
		mode = null
	}: {
		texts: readonly (string | null | undefined)[];
		specs: readonly PromptSpecLike[];
		formData?: Record<string, unknown> | null;
		presetId?: string | null;
		mode?: string | null;
	} = $props();

	const resolver = createKitResolver((request) => api.resolveKitCitations(request));
	onDestroy(() => {
		resolver.cancel();
		kitNotices.set([]);
	});

	let cites = $derived.by(() => {
		const capable = kitCapableFields(specs);
		const found: KitCite[] = [];
		for (const text of texts) {
			if (!text || !text.includes('@[')) continue;
			for (const match of text.matchAll(resourceMarkerRegex())) {
				const key = parseKitKey(match[2]);
				if (key) found.push({ key, accepted: capable.get(match[1]) ?? null });
			}
		}
		return found;
	});
	let fields = $derived(kitFieldsOf(specs, formData));
	let localNotes = $derived(skippedNotes(cites, $kitDirectory));
	let serverLines = $derived($resolver.result ? skipLines($resolver.result.skipped) : null);

	$effect(() => {
		resolver.request(
			buildResolveRequest({
				texts,
				loaded: fields.flatMap((entry) => entry.rows.map((row) => row.kit)),
				field: defaultKitField(fields),
				presetId,
				mode
			})
		);
	});

	$effect(() => {
		kitNotices.set($resolver.result?.skipped ?? []);
	});

	$effect(() => {
		void ensureKits(cites.map((cite) => cite.key.kitId));
	});
</script>

{#if serverLines}
	{#each serverLines as line (line)}
		<div class="flex items-start gap-2.5 rounded-lg border border-warning/25 bg-warning/10 px-3 py-2 text-sm text-fg" data-kit-skipped-line>
			<Icon name="warning" className="mt-0.5 h-4 w-4 flex-shrink-0 text-warning" />
			<p>{line}</p>
		</div>
	{/each}
{:else}
	{#each localNotes as note (note.kind)}
		<div class="flex items-start gap-2.5 rounded-lg border border-warning/25 bg-warning/10 px-3 py-2 text-sm text-fg" data-kit-skipped-line>
			<Icon name="warning" className="mt-0.5 h-4 w-4 flex-shrink-0 text-warning" />
			<p>
				This model does not take {note.kindLabel}:
				{#each note.names as name, index (name)}<span class="font-mono">{name}</span>{index < note.names.length - 1 ? ', ' : ''}{/each}
				{note.names.length === 1 ? 'is' : 'are'} skipped.
			</p>
		</div>
	{/each}
{/if}
