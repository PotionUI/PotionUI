<script lang="ts">
	import { processMarkdown } from '$lib/utils/markdown';
	import { docsStore } from '$lib/stores/docs';
	import { Button, Spinner } from '$lib/components/ui';
	import { isModelMeta, isTechniqueMeta } from '$lib/utils/docsMeta';
	import type { DocItem, DocRefs, ModelMeta, TechniqueMeta } from '$lib/types/api';
	import HooksCatalog from './live/HooksCatalog.svelte';
	import FieldTypesReference from './live/FieldTypesReference.svelte';
	import PipesReference from './live/PipesReference.svelte';
	import OutputTypesReference from './live/OutputTypesReference.svelte';
	import TemplateFunctionsReference from './live/TemplateFunctionsReference.svelte';
	import IconsReference from './live/IconsReference.svelte';
	import FrontendKit from './live/FrontendKit.svelte';
	import TechniqueHeader from './TechniqueHeader.svelte';
	import ModelHeader from './ModelHeader.svelte';

	export let item: DocItem;
	// Cross-doc navigation (technique -> model chips, model -> technique rows).
	// Optional so this component still works standalone / in isolation tests.
	export let onNavigate: ((docId: string) => void) | undefined = undefined;

	let markdown = '';
	let title = '';
	let meta: TechniqueMeta | ModelMeta | null | undefined = null;
	let refs: DocRefs | null | undefined = null;
	let loading = false;
	let error: string | null = null;

	// processMarkdown doesn't emit heading ids; add stable slugs so in-page
	// anchor links (#some-heading) work without touching the shared util.
	function withHeadingAnchors(html: string): string {
		const seen = new Set<string>();
		return html.replace(/<h([1-3])([^>]*)>(.*?)<\/h\1>/g, (match, level, attrs, inner) => {
			if (/\bid=/.test(attrs)) return match;
			const text = inner.replace(/<[^>]+>/g, '');
			let slug = text
				.toLowerCase()
				.trim()
				.replace(/[^a-z0-9\s-]/g, '')
				.replace(/\s+/g, '-');
			if (!slug) return match;
			let unique = slug;
			let i = 2;
			while (seen.has(unique)) {
				unique = `${slug}-${i++}`;
			}
			seen.add(unique);
			return `<h${level}${attrs} id="${unique}">${inner}</h${level}>`;
		});
	}

	// Documentation files are source-wrapped for maintainability. Treat those
	// single newlines as soft wrapping so prose responds to the available pane
	// width instead of appearing capped at the source file's column length.
	$: html = markdown
		? withHeadingAnchors(processMarkdown(markdown, { softLineBreaks: 'space' }))
		: '';

	async function load(id: string) {
		// A typed technique/model doc's `type` is still 'markdown' (only its
		// `doc_type` differs) -- it carries a markdown body rendered below its
		// typed header, same as any other markdown doc. Only 'live' skips this
		// fetch entirely.
		if (item.type === 'live') return;

		const cached = docsStore.getCachedContent(id);
		if (cached) {
			markdown = cached.markdown;
			title = cached.title;
			meta = cached.meta;
			refs = cached.refs;
			return;
		}

		loading = true;
		error = null;
		const result = await docsStore.loadContent(id);
		loading = false;
		if (result) {
			markdown = result.markdown;
			title = result.title;
			meta = result.meta;
			refs = result.refs;
		} else {
			error = 'Failed to load this document.';
		}
	}

	// `item` is an object prop, so any parent re-render marks it dirty — including
	// re-renders caused by loadContent() itself writing error state to the store.
	// Guard on the id so each doc is fetched once per selection, not in a loop.
	let attemptedId: string | null = null;
	$: if (item.id !== attemptedId) {
		attemptedId = item.id;
		load(item.id);
	}

	function retry() {
		load(item.id);
	}
</script>

<div class="w-full min-w-0 max-w-none px-4 py-5 sm:px-6 sm:py-8 lg:px-8">
	{#if item.type === 'live'}
		<h1 class="text-2xl font-bold mb-4 text-fg">{item.title}</h1>
		{#if item.live_kind === 'hooks'}
			<HooksCatalog />
		{:else if item.live_kind === 'field-types'}
			<FieldTypesReference />
		{:else if item.live_kind === 'pipes'}
			<PipesReference />
		{:else if item.live_kind === 'output-types'}
			<OutputTypesReference />
		{:else if item.live_kind === 'template-functions'}
			<TemplateFunctionsReference />
		{:else if item.live_kind === 'icons'}
			<IconsReference />
		{:else if item.live_kind === 'frontend-kit'}
			<FrontendKit />
		{:else}
			<p class="text-sm text-fg-subtle">Unknown live reference type "{item.live_kind}".</p>
		{/if}
	{:else if loading}
		<div class="flex items-center justify-center py-16">
			<Spinner size="lg" />
		</div>
	{:else if error}
		<div class="py-8 space-y-3">
			<p class="text-sm text-danger">{error}</p>
			<Button variant="secondary" size="sm" onclick={retry}>Retry</Button>
		</div>
	{:else}
		<!-- Graceful degradation: a 'technique'/'model' doc whose content hasn't
			 been typed yet (meta absent, or a plugin doc that never adopts the
			 schema) renders exactly like a plain markdown doc. `type` stays
			 'markdown'/'live' (render kind) -- `doc_type` carries the typed
			 frontmatter kind (see types/api.ts's DocItem docstring). -->
		{#if item.doc_type === 'technique' && isTechniqueMeta(meta)}
			<TechniqueHeader {meta} {refs} {onNavigate} />
		{:else if item.doc_type === 'model' && isModelMeta(meta)}
			<ModelHeader {meta} {refs} status={item.status} {onNavigate} />
		{/if}
		<div class="text-[15px] leading-relaxed text-fg-muted">
			{@html html}
		</div>
	{/if}
</div>
