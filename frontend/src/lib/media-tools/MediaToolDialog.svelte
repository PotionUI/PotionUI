<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { hasIcon } from '$lib/utils/IconLibrary';
	import { previewMediaTool } from '$lib/services/api/mediaTools';
	import { stillEffects, stillPreview } from '$lib/tool-tabs/image/shaderEffect';
	import { supportsWebGL2 } from '$lib/effects/ShaderChain';
	import { selectionBounds, toolAvailability } from './catalog';
	import { parseMediaToolFailure } from './errors';
	import { executeMediaTool } from './execute';
	import type { AnnounceSetting } from './announce';
	import {
		canReplace,
		NOT_IN_LIBRARY,
		normalizeSources,
		resolveToolInput,
		type ResolvedToolInput
	} from './inputs';
	import { buildParams, initialValues, paramIssues } from './params';
	import { createPreviewController, type PreviewState } from './preview';
	import { computeReadout, describeSource } from './readout';
	import { mediaToolRuns, type MediaToolRunsStore, type RunHandle } from './runs';
	import ClipList from './ClipList.svelte';
	import PreviewPane from './PreviewPane.svelte';
	import ToolFooter from './ToolFooter.svelte';
	import ToolForm from './ToolForm.svelte';
	import type {
		MediaToolInputRef,
		MediaToolItem,
		MediaToolOutputMode,
		MediaToolSource,
		ToolDTO,
		ToolScope
	} from './types';

	let {
		tool,
		items,
		scope,
		detach = false,
		announce = undefined,
		runs = mediaToolRuns,
		onClose,
		onResult
	}: {
		tool: ToolDTO;
		items: MediaToolSource[];
		scope: ToolScope;
		detach?: boolean;
		announce?: AnnounceSetting;
		runs?: MediaToolRunsStore;
		onClose: () => void;
		onResult: (result: { items: MediaToolItem[]; replaced: boolean }) => void | Promise<void>;
	} = $props();

	const SHADER_DEBOUNCE_MS = 40;

	let manyToOne = $derived(tool.cardinality === 'many_to_one');
	let bounds = $derived(selectionBounds(tool));

	let clips = $state<MediaToolSource[]>(untrack(() => normalizeSources(items, scope)));
	let values = $state<Record<string, unknown>>(untrack(() => initialValues(tool)));
	let mode = $state<MediaToolOutputMode>('new');
	let phase = $state<'edit' | 'working'>('edit');
	let failure = $state<string | null>(null);
	let fieldErrors = $state<Record<string, string>>({});
	let activeRun = $state<RunHandle | null>(null);
	let resolution = $state<{ ready: boolean; refs: MediaToolInputRef[]; reason: string | null }>({
		ready: false,
		refs: [],
		reason: null
	});
	let preview = $state<PreviewState>({ url: null, loading: false, error: null });

	let disposed = false;
	const refCache = new Map<string, Promise<ResolvedToolInput>>();

	let sources = $derived(clips);
	let primary = $derived(sources[0]);
	let params = $derived(buildParams(tool.params, values));
	let previewKey = $derived(JSON.stringify(params));
	let hasIssues = $derived(Object.keys(paramIssues(tool.params, values)).length > 0);
	let firstIssue = $derived(Object.values(paramIssues(tool.params, values))[0] ?? null);
	let availability = $derived(toolAvailability(tool, sources));
	let readout = $derived(computeReadout(tool, params, sources));
	const shaderEffect = untrack(() => (supportsWebGL2() ? (stillEffects([tool])[0] ?? null) : null));
	let shaderPreview = $derived(!!shaderEffect && sources.length === 1 && primary?.kind === 'image');
	let previewable = $derived(
		shaderPreview || (tool.preview && sources.length === 1 && (primary?.kind === 'image' || primary?.kind === 'video'))
	);
	let replaceable = $derived(
		scope === 'library' &&
			tool.cardinality === 'one_to_one' &&
			tool.produces === 'same' &&
			canReplace(resolution.refs)
	);
	let effectiveMode = $derived<MediaToolOutputMode>(replaceable ? mode : 'new');

	let blocked = $derived.by(() => {
		if (!resolution.ready) return 'Preparing this media…';
		if (resolution.reason) return resolution.reason;
		if (!availability.enabled) return availability.reason ?? null;
		return firstIssue ? 'Fix the highlighted setting to continue.' : null;
	});
	let canApply = $derived(phase === 'edit' && blocked === null);

	let runState = $derived($runs[primary?.id ?? '']);

	let headerName = $derived(
		sources.length > 1 ? `${sources.length} ${manyToOne ? 'clips' : 'items'}` : (primary?.filename ?? '')
	);
	let applyLabel = $derived(tool.id === 'concat' ? 'Join' : 'Apply');

	let note = $derived.by(() => {
		const kind = primary?.kind === 'audio' ? 'clip' : (primary?.kind ?? 'item');
		if (scope === 'field') {
			return `The result replaces this ${kind} in the field. The library copy stays as it is.`;
		}
		if (scope === 'history') {
			return tool.cardinality === 'one_to_many'
				? 'Saves copies to your Library. The generation is not changed.'
				: 'Saves a copy to your Library. The generation is not changed.';
		}
		if (manyToOne) return `Saved as a new ${tool.produces === 'same' ? kind : tool.produces}.`;
		if (tool.cardinality === 'one_to_many') return 'Saved as new items.';
		if (sources.length > 1) return 'Each result is saved as a new item.';
		if (tool.produces !== 'same') return `Saved as a new ${tool.produces}.`;
		return null;
	});

	$effect(() => {
		const list = sources;
		let current = true;
		resolution = { ready: false, refs: [], reason: null };
		Promise.all(
			list.map((source) => {
				let pending = refCache.get(source.id);
				if (!pending) {
					pending = resolveToolInput(source);
					refCache.set(source.id, pending);
				}
				return pending;
			})
		).then((results) => {
			if (!current || disposed) return;
			const refs: MediaToolInputRef[] = [];
			for (const result of results) {
				if (!result.ref) {
					resolution = { ready: true, refs: [], reason: result.reason ?? NOT_IN_LIBRARY };
					return;
				}
				refs.push(result.ref);
			}
			resolution = { ready: true, refs, reason: null };
		});
		return () => {
			current = false;
		};
	});

	const previewer = createPreviewController({
		request: (signal) =>
			shaderPreview && shaderEffect && primary
				? stillPreview(primary.url, shaderEffect, buildParams(tool.params, values), signal)
				: previewMediaTool({ tool: tool.id, params: buildParams(tool.params, values), input: resolution.refs[0] }, signal),
		onChange: (state) => (preview = state),
		debounceMs: shaderEffect ? SHADER_DEBOUNCE_MS : undefined
	});
	let firstPreview = true;

	$effect(() => {
		if (!previewKey || !previewable || !resolution.ready || resolution.reason || hasIssues) {
			if (previewable) previewer.cancel();
			return;
		}
		const immediate = firstPreview;
		firstPreview = false;
		previewer.schedule(immediate);
	});

	onDestroy(() => {
		disposed = true;
		previewer.destroy();
	});

	function setValue(name: string, next: unknown) {
		values = { ...values, [name]: next };
		if (fieldErrors[name]) {
			const { [name]: _cleared, ...rest } = fieldErrors;
			fieldErrors = rest;
		}
	}

	function moveClip(from: number, to: number) {
		const next = [...clips];
		const [moved] = next.splice(from, 1);
		next.splice(to, 0, moved);
		clips = next;
	}

	function removeClip(index: number) {
		if (clips.length <= bounds.min) return;
		clips = clips.filter((_, position) => position !== index);
	}

	function requestClose() {
		if (phase === 'working') return;
		onClose();
	}

	async function apply() {
		if (!canApply) return;
		failure = null;
		fieldErrors = {};
		phase = 'working';
		try {
			const handle = await executeMediaTool({
				label: tool.label,
				steps: [{ tool: tool.id, params }],
				sources,
				refs: resolution.refs,
				mode: effectiveMode,
				announce: announce ?? detach,
				runs
			});

			if (detach) {
				void handle.done.then((outcome) => {
					if (outcome.status === 'completed') {
						void onResult({ items: outcome.items, replaced: outcome.replaced });
					}
				});
				onClose();
				return;
			}

			activeRun = handle;
			const outcome = await handle.done;
			activeRun = null;
			if (disposed) return;
			if (outcome.status === 'completed') {
				await onResult({ items: outcome.items, replaced: outcome.replaced });
				onClose();
				return;
			}
			if (outcome.status === 'failed') failure = outcome.message;
			phase = 'edit';
		} catch (error) {
			activeRun = null;
			if (disposed) return;
			const refusal = parseMediaToolFailure(error);
			failure = refusal.message;
			if (refusal.field) fieldErrors = { [refusal.field]: refusal.message };
			phase = 'edit';
		}
	}

	async function cancelRun() {
		if (!activeRun) return;
		await runs.cancelRun(activeRun.runId);
	}
</script>

<BaseModal
	isOpen={true}
	title={tool.label}
	sizeClass="md:w-[620px]"
	closeable={phase === 'edit'}
	on:close={requestClose}
>
	<svelte:fragment slot="headerIcon">
		<Icon
			name={hasIcon(tool.icon) ? tool.icon : 'adjustments-horizontal'}
			className="h-3.5 w-3.5 text-signal"
		/>
	</svelte:fragment>

	<svelte:fragment slot="header">
		<span class="block min-w-0 max-w-[16rem] truncate font-mono text-sm text-fg-subtle" data-tool-subject>
			{headerName}
		</span>
	</svelte:fragment>

	<div class="flex flex-col gap-3.5 px-5 py-4" data-tool-dialog={tool.id}>
		{#if manyToOne}
			<ClipList clips={sources} min={bounds.min} max={bounds.max} onMove={moveClip} onRemove={removeClip} />
			<div class="h-px bg-line"></div>
		{:else if previewable && primary}
			<PreviewPane
				beforeUrl={primary.url}
				beforeKind={primary.kind === 'video' ? 'video' : 'image'}
				afterUrl={preview.url}
				loading={preview.loading}
				error={preview.error}
				aspect={primary.width && primary.height ? primary.width / primary.height : 4 / 3}
			/>
		{:else if primary}
			<div class="flex items-center gap-3" data-tool-source>
				<span class="grid h-10 w-12 shrink-0 place-items-center overflow-hidden rounded bg-surface-3">
					{#if primary.kind === 'image'}
						<img src={primary.url} alt="" class="h-full w-full object-cover" />
					{:else if primary.kind === 'video'}
						<video src={primary.url} muted playsinline preload="metadata" class="h-full w-full object-cover" aria-hidden="true"></video>
					{:else}
						<Icon name="audio" className="h-4 w-4 text-fg-subtle" />
					{/if}
				</span>
				<span class="flex min-w-0 flex-col gap-0.5">
					<span class="truncate text-sm text-fg">{primary.filename}</span>
					<span class="truncate font-mono text-sm tabular-nums text-fg-subtle">
						{sources.length > 1 ? `${sources.length} items` : describeSource(primary)}
					</span>
				</span>
			</div>
		{/if}

		<ToolForm params={tool.params} {values} errors={fieldErrors} onChange={setValue} />

		{#if readout}
			<div
				class="flex items-center justify-between gap-3 rounded border border-line bg-surface-2 px-3 py-2"
				data-tool-readout
			>
				<span class="label !mb-0">Result</span>
				<span class="min-w-0 truncate font-mono text-sm tabular-nums text-fg-muted">{readout}</span>
			</div>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<ToolFooter
			mode={effectiveMode}
			showModes={replaceable}
			{note}
			{applyLabel}
			applyIcon={tool.id !== 'concat'}
			disabled={!canApply}
			working={phase === 'working'}
			progress={runState?.progress ?? 0}
			statusLabel={runState?.message ?? null}
			cancelling={runState?.cancelling ?? false}
			{failure}
			{blocked}
			onMode={(next) => (mode = next)}
			onCancel={requestClose}
			onApply={apply}
			onCancelRun={cancelRun}
		/>
	</svelte:fragment>
</BaseModal>
