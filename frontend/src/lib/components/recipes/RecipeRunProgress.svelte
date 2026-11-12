<script lang="ts">
	import GatedModelLink from '$lib/components/models/GatedModelLink.svelte';
	import type { Snippet } from 'svelte';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import type { SetupRun } from '$lib/services/api/setup';
	import type { RecipeRunActions } from './runActions';
	import {
		runBadgeVariant,
		runStatusLabel,
		manifestStepBadgeVariant,
		manifestStepStatusLabel,
		resolveStepGroups,
		runManifestProgressSummary,
		runProgressPercent,
		consentKindLabel,
		stepDuration,
		smokeResultMedia,
		stepProgressLabel,
		stepProgressPercent,
		isConsentStatus,
		runNeedsConsent,
		canRetryRun,
		extractConsentRequest,
		computeTransferStats,
		type TransferStats
	} from '$lib/utils/setupRunDisplay';
	import { formatBytes, formatDuration } from '$lib/utils/format';
	import { Alert, Badge, Button } from '$lib/components/ui';
	import MeshPreview from '$lib/components/workbench/renderers/MeshPreview.svelte';
	import ConsentVariantPicker from './ConsentVariantPicker.svelte';
	import ProviderKeyDisclosure from './ProviderKeyDisclosure.svelte';
	import ModeConsentGate from './modeInstall/ModeConsentGate.svelte';
	import ModeInstallDownloads from './modeInstall/ModeInstallDownloads.svelte';
	import ModeInstallDone from './modeInstall/ModeInstallDone.svelte';
	import ModeInstallSteps from './modeInstall/ModeInstallSteps.svelte';
	import { modeContextOf, preselectOf, replanFor } from './modeInstall/modeInstallActions';
	import {
		extractApprovedPlan,
		extractDownloadProgress,
		extractModesConsent,
		isModesRun
	} from './modeInstall/modeInstallModel';
	import type { ModesGrantBody } from '$lib/services/api/recipeModes';

	/**
	 * The live view of one recipe run: progress summary, consent gate, failure
	 * with retry, and the ordered step rows. Shared by the /setup wizard and
	 * Admin -> Recipes so both surfaces render a run identically.
	 *
	 * Polling belongs to the caller — this component never fetches the run, it
	 * only hands back whatever a user action returned via `onRunUpdated`, so
	 * the caller can reschedule its own poll. The two mutating calls come in as
	 * `actions` because the wizard drives the setup endpoints and the admin tab
	 * drives the recipes ones; the provider-credential call is the same plugin
	 * settings endpoint on both surfaces, so it stays here.
	 */
	let {
		run,
		title = 'Guided setup',
		fetchError = '',
		actions,
		onRunUpdated,
		onStartOver,
		completed,
		consentNote,
		overallBar = false,
		guided = false
	}: {
		run: SetupRun;
		title?: string;
		/** Caller-owned "couldn't reach the server" notice, shown under the header. */
		fetchError?: string;
		actions: RecipeRunActions;
		onRunUpdated: (run: SetupRun) => void;
		/** Escape hatch out of a failed run; the link is omitted when absent. */
		onStartOver?: () => void;
		/** Rendered between the header and the consent gate — the wizard's
		 * first-generation handoff, which no other surface has. */
		completed?: Snippet;
		consentNote?: Snippet;
		overallBar?: boolean;
		guided?: boolean;
	} = $props();

	const resolvedSteps = $derived(resolveStepGroups(run));
	const consentGroup = $derived(
		runNeedsConsent(run)
			? (resolvedSteps.groups.find((g) => isConsentStatus(g.status as any)) ?? null)
			: null
	);
	const consentRequest = $derived(consentGroup ? extractConsentRequest(consentGroup.latest) : null);
	const modesConsent = $derived(consentGroup ? extractModesConsent(consentGroup.latest) : null);
	const modesRun = $derived(isModesRun(run));
	const approvedPlan = $derived(modesRun ? extractApprovedPlan(run) : null);
	const modeContext = $derived(modesRun ? modeContextOf(run) : { presetName: '', modes: {} });
	const downloadGroup = $derived(
		modesRun
			? (resolvedSteps.groups.find(
					(g) => g.kind === 'artifacts.fetch' && g.status === 'running' && extractDownloadProgress(g.latest)
				) ?? null)
			: null
	);
	const downloadProgress = $derived(downloadGroup ? extractDownloadProgress(downloadGroup.latest) : null);
		const newModeIds = $derived(
		approvedPlan ? approvedPlan.modes.filter((id) => modeContext.modes[id] && !modeContext.modes[id].ready) : []
	);
	const modesTitle = $derived.by(() => {
		if (!modesRun || !approvedPlan) return null;
		const count = newModeIds.length;
		const noun = `${count} ${count === 1 ? 'mode' : 'modes'}`;
		return run.status === 'completed' ? `Installed ${noun}` : `Installing ${noun}`;
	});

	// The server reports plain byte counts per poll; speed/ETA are reconstructed
	// here from two consecutive polls, one sample per step —
	// `lastProgressSamples` is a plain mutated Map (not reactive state)
	// precisely so recording into it never re-triggers this block.
	const lastProgressSamples = new Map<string, { bytes: number; at: number }>();
	let transferStatsByStep = $state<Record<string, TransferStats>>({});

	$effect(() => {
		const next: Record<string, TransferStats> = {};
		for (const group of resolvedSteps.groups) {
			const current = group.latest?.progress_current;
			if (current == null) continue;
			const sample = { bytes: current, at: Date.now() };
			next[group.stepKey] = computeTransferStats(
				lastProgressSamples.get(group.stepKey) ?? null,
				sample,
				group.latest?.progress_total ?? null
			);
			lastProgressSamples.set(group.stepKey, sample);
		}
		transferStatsByStep = next;
	});

	let retryBusy = $state(false);
	let retryError = $state('');
	let consentBusy = $state(false);
	let consentError = $state('');
	let cancelBusy = $state(false);
	let cancelError = $state('');

	let selections = $state.raw<Record<string, string>>({});
	let selectionsStepKey = '';

	$effect(() => {
		const key = consentGroup?.stepKey ?? '';
		if (key !== selectionsStepKey) {
			selectionsStepKey = key;
			selections = {};
		}
	});

	function pickVariant(slotId: string, variantId: string) {
		selections = { ...selections, [slotId]: variantId };
	}

	function errorText(err: any, fallback: string): string {
		return getApiErrorMessage(err, fallback);
	}

	async function approveModes(body: ModesGrantBody) {
		if (!consentGroup || consentBusy) return false;
		consentBusy = true;
		consentError = '';
		try {
			const { selections, ...extra } = body;
			onRunUpdated(await actions.grantConsent(run.id, consentGroup.stepKey, selections, extra));
			return true;
		} catch (err: any) {
			consentError = errorText(err, "Couldn't approve the download.");
			return false;
		} finally {
			consentBusy = false;
		}
	}

	async function approveConsent() {
		if (!consentGroup || consentBusy) return;
		consentBusy = true;
		consentError = '';
		try {
			onRunUpdated(await actions.grantConsent(run.id, consentGroup.stepKey, selections));
		} catch (err: any) {
			consentError = errorText(err, "Couldn't approve the download.");
		} finally {
			consentBusy = false;
		}
	}

	async function cancelRun() {
		if (cancelBusy) return;
		cancelBusy = true;
		cancelError = '';
		try {
			onRunUpdated(await actions.applyAction(run.id, 'cancel'));
		} catch (err: any) {
			cancelError = errorText(err, "Couldn't cancel setup.");
		} finally {
			cancelBusy = false;
		}
	}

	async function retryRun() {
		if (retryBusy) return;
		const runId = run.id;
		const previous = run;
		// Optimistic flip so the failed state doesn't linger while the request
		// is in flight.
		onRunUpdated({ ...previous, status: 'running', error_code: null, safe_error_detail: null });
		retryBusy = true;
		retryError = '';
		try {
			onRunUpdated(await actions.applyAction(runId, 'retry_step'));
		} catch (err: any) {
			onRunUpdated(previous);
			retryError = errorText(err, "Couldn't start the retry.");
		} finally {
			retryBusy = false;
		}
	}
</script>

<div class="space-y-4">
	{#if guided}
		<p class="text-sm text-fg-muted">{runManifestProgressSummary(resolvedSteps)}</p>
	{:else}
		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0">
				<h2 class="text-sm font-semibold text-fg">{modesTitle ?? title}</h2>
				<p class="text-sm text-fg-muted mt-0.5">{runManifestProgressSummary(resolvedSteps)}</p>
			</div>
			<Badge variant={runBadgeVariant(run.status)}>{runStatusLabel(run.status)}</Badge>
		</div>
	{/if}

	{#if overallBar && resolvedSteps.groups.length > 0}
		<div
			class="h-1.5 overflow-hidden rounded-sm bg-surface-3"
			role="progressbar"
			aria-valuemin="0"
			aria-valuemax="100"
			aria-valuenow={runProgressPercent(resolvedSteps)}
			aria-label="Setup progress"
		>
			<div
				class="h-full bg-signal-solid transition-all duration-300"
				style="width: {runProgressPercent(resolvedSteps)}%"
			></div>
		</div>
	{/if}

	{#if fetchError}
		<p class="text-xs text-fg-subtle">{fetchError}</p>
	{/if}

	{@render completed?.()}

	{#if consentGroup && modesConsent}
		<ModeConsentGate
			{guided}
			request={modesConsent}
			preselect={preselectOf(run)}
			busy={consentBusy}
			error={consentError}
			cancelBusy={cancelBusy}
			onApprove={approveModes}
			onCancelRun={cancelRun}
			onReplan={replanFor(run.recipe_id)}
		/>
		{#if cancelError}
			<p class="text-xs text-danger">{cancelError}</p>
		{/if}
	{:else if consentGroup && consentRequest}
		<div class="rounded border border-signal/30 bg-signal/5 px-3 py-3 space-y-3">
			<div>
				{#if !guided}
					<p class="text-sm font-semibold text-signal">Needs your go-ahead</p>
				{/if}
				<p class="text-sm text-fg-muted {guided ? '' : 'mt-0.5'}">
					To continue, this step needs to download the following:
				</p>
			</div>

			{#if consentRequest.slots && consentRequest.slots.length > 0}
				<ConsentVariantPicker
					gpu={consentRequest.gpu ?? null}
					slots={consentRequest.slots}
					{selections}
					onPick={pickVariant}
				/>
			{:else}
				<ul class="space-y-1">
					{#each consentRequest.artifacts as artifact (artifact.id)}
						<li class="flex items-center justify-between gap-3 text-sm">
							<span class="min-w-0 flex items-center gap-1.5">
								<span class="text-fg truncate">{artifact.display_name}</span>
								{#if consentKindLabel(artifact.kind)}
									<span class="text-fg-subtle shrink-0">{consentKindLabel(artifact.kind)}</span>
								{/if}
								{#if artifact.gated}
									<Badge variant="warning" size="sm">gated</Badge>
									<GatedModelLink source={artifact} />
								{/if}
							</span>
							{#if artifact.size_bytes != null}
								<span class="font-mono tabular-nums text-fg-subtle shrink-0">
									{formatBytes(artifact.size_bytes)}
								</span>
							{/if}
						</li>
					{/each}
				</ul>

				{#if consentRequest.total_bytes != null}
					<div class="flex items-center justify-between text-sm border-t border-signal/20 pt-2">
						<span class="text-fg-muted">Total</span>
						<span class="font-mono tabular-nums text-fg">{formatBytes(consentRequest.total_bytes)}</span>
					</div>
				{/if}
			{/if}

			{#if consentRequest.warnings && consentRequest.warnings.length > 0}
				<Alert variant="warning" density="compact">
					{#each consentRequest.warnings as warning (warning)}
						<p>{warning}</p>
					{/each}
				</Alert>
			{/if}

			{@render consentNote?.()}

			{#each consentRequest.providers ?? [] as provider (provider.id)}
				<ProviderKeyDisclosure {provider} generic={guided} />
			{/each}

			<div class="flex items-center justify-between gap-3 pt-1">
				<Button size="sm" variant="primary" loading={consentBusy} onclick={approveConsent}>
					Approve and download
				</Button>
				<button
					type="button"
					class="text-xs text-fg-subtle hover:text-fg-muted underline decoration-dotted disabled:opacity-50"
					disabled={cancelBusy}
					onclick={cancelRun}
				>
					Cancel setup instead
				</button>
			</div>
			{#if consentError}
				<p class="text-xs text-danger">{consentError}</p>
			{/if}
			{#if cancelError}
				<p class="text-xs text-danger">{cancelError}</p>
			{/if}
		</div>
	{/if}

	{#if downloadProgress}
		<ModeInstallDownloads progress={downloadProgress} cancelBusy={cancelBusy} cancelError={cancelError} onCancel={cancelRun} />
	{/if}

	{#if modesRun && run.status === 'completed' && approvedPlan}
		<ModeInstallDone
			presetId={approvedPlan.presetId}
			presetName={modeContext.presetName}
			modes={newModeIds.map((id) => ({ id, label: modeContext.modes[id].label, description: modeContext.modes[id].description }))}
			bytes={approvedPlan.bytes}
		/>
	{/if}

	{#if run.status === 'failed'}
		{@const failedRunStatus = run.status}
		{@const interrupted = run.error_code === 'RUN_INTERRUPTED'}
		{@const failedAttempt = [...resolvedSteps.groups].reverse().find((g) => g.latest?.status === 'failed')?.latest ?? null}
		<Alert
			variant={interrupted ? 'warning' : 'danger'}
			density="compact"
			title={interrupted ? 'Setup was interrupted' : "Setup couldn't finish"}
		>
			{#if interrupted}
				<p>Files already downloaded are kept. Continue to pick up where it stopped.</p>
			{:else if run.safe_error_detail}
				<p>{run.safe_error_detail}</p>
			{/if}
			{#if !interrupted && failedAttempt?.safe_suggested_action}
				<p class="text-xs mt-1">{failedAttempt.safe_suggested_action}</p>
			{/if}
			{#if !interrupted && failedAttempt?.safe_technical_detail}
				<details class="mt-1">
					<summary class="text-xs cursor-pointer">Details</summary>
					<p class="mt-1 break-words font-mono text-xs text-fg-muted">{failedAttempt.safe_technical_detail}</p>
				</details>
			{/if}
			{#if retryError}
				<p class="text-xs mt-1">{retryError}</p>
			{/if}
			{#if onStartOver}
				<div class="mt-2">
					<button
						type="button"
						class="text-xs text-fg-subtle hover:text-fg-muted underline decoration-dotted"
						onclick={onStartOver}
					>
						Start over with a different recipe instead
					</button>
				</div>
			{/if}
			{#snippet actions()}
				{#if canRetryRun(failedRunStatus)}
					<Button size="sm" variant="secondary" loading={retryBusy} onclick={retryRun}>
						{interrupted ? 'Continue' : 'Try again'}
					</Button>
				{/if}
			{/snippet}
		</Alert>
	{/if}

	{#if modesRun && resolvedSteps.groups.length > 0}
		<ModeInstallSteps
			groups={resolvedSteps.groups}
			plan={approvedPlan}
			modeCount={newModeIds.length}
			progress={downloadProgress}
		/>
	{:else if resolvedSteps.groups.length > 0}
		<div class="space-y-2">
			{#each resolvedSteps.groups as group (group.stepKey)}
				{@const isConsent = isConsentStatus(group.status as any)}
				{@const isPending = group.status === 'pending'}
				{@const duration = group.latest ? stepDuration(group.latest) : null}
				{@const progress = group.status === 'running' ? stepProgressLabel(group.latest) : null}
				{@const percent = group.status === 'running' ? stepProgressPercent(group.latest) : null}
				{@const transfer = transferStatsByStep[group.stepKey]}
				<div
					class="rounded border px-3 py-2 {isConsent
						? 'border-signal/30 bg-signal/5'
						: isPending
							? 'border-line bg-surface-1'
							: 'border-line bg-surface-2'}"
				>
					<div class="flex items-center justify-between gap-3">
						<div class="flex items-center gap-2 min-w-0">
							<p class="text-sm font-medium {isPending ? 'text-fg-subtle' : 'text-fg'} truncate">
								{group.title}
							</p>
							<Badge size="sm" variant={manifestStepBadgeVariant(group.status)}>
								{manifestStepStatusLabel(group.status)}
							</Badge>
						</div>
						{#if duration}
							<span class="text-xs font-mono tabular-nums text-fg-subtle shrink-0">
								{duration}
							</span>
						{/if}
					</div>

					{#if isConsent}
						<p class="text-sm text-fg-muted mt-1">
							This step needs your go-ahead before it can continue.
						</p>
					{/if}

					{#if group.status === 'running' && percent != null && group.stepKey !== downloadGroup?.stepKey}
						<div class="mt-2 h-1.5 bg-surface-3 rounded-sm overflow-hidden">
							<div
								class="h-full bg-signal-solid transition-all duration-300"
								style="width: {percent}%"
							></div>
						</div>
					{/if}

					{#if (progress || percent != null) && group.stepKey !== downloadGroup?.stepKey}
						<div
							class="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs font-mono tabular-nums text-fg-subtle mt-1"
						>
							{#if percent != null}<span>{percent}%</span>{/if}
							{#if progress}<span>{progress}</span>{/if}
							{#if transfer?.bytesPerSecond}<span>{formatBytes(transfer.bytesPerSecond)}/s</span>{/if}
							{#if transfer?.etaMs != null}<span>ETA {formatDuration(transfer.etaMs)}</span>{/if}
						</div>
						{#if group.kind === 'artifacts.fetch'}
							<a
								href="/admin?tab=downloads"
								class="text-xs text-fg-subtle hover:text-fg-muted underline decoration-dotted mt-1 inline-block"
							>
								View in Downloads
							</a>
						{/if}
					{/if}

					{#if group.kind === 'generation.smoke' && group.latest?.status === 'succeeded'}
						{@const media = smokeResultMedia(group.latest)}
						{#if media}
							<div class="mt-2 flex flex-col gap-1.5">
								{#if media.kind === 'image'}
									<a href={api.getGenerationImageURL(media.generationId, media.filename)} target="_blank" rel="noreferrer" class="inline-block w-fit">
										<img
											src={api.getGenerationImageURL(media.generationId, media.filename)}
											alt="Your first generation"
											class="max-h-64 w-auto rounded border border-line bg-black"
											loading="lazy"
										/>
									</a>
								{:else if media.kind === 'video'}
									<video
										src={api.getGenerationImageURL(media.generationId, media.filename)}
										class="max-h-64 w-auto rounded border border-line bg-black"
										controls
										muted
										loop
										playsinline
									></video>
								{:else if media.kind === 'audio'}
									<audio src={api.getGenerationImageURL(media.generationId, media.filename)} controls class="w-full max-w-md"></audio>
								{:else if media.kind === 'mesh'}
									{@const meshUrl = api.getGenerationImageURL(media.generationId, media.filename)}
									<div class="h-64 w-full max-w-md rounded border border-line bg-black overflow-hidden">
										<MeshPreview file={{ url: meshUrl, originalUrl: meshUrl }} />
									</div>
								{/if}
								<span class="text-xs font-mono text-fg-subtle truncate">{media.filename}</span>
							</div>
						{/if}
					{/if}

					{#if (group.latest?.status === 'failed' || group.latest?.status === 'action_required') && group.latest.safe_error_detail}
						<details class="mt-1">
							<summary
								class="text-sm cursor-pointer {group.latest.status === 'failed' ? 'text-danger' : 'text-warning'}"
							>
								Why it didn't finish
							</summary>
							<p class="text-sm text-fg-muted mt-1">
								{group.latest.safe_error_detail}
							</p>
							{#if group.latest.safe_suggested_action}
								<p class="text-sm text-fg-muted mt-1">{group.latest.safe_suggested_action}</p>
							{/if}
							{#if group.latest.safe_technical_detail}
								<details class="mt-1">
									<summary class="text-sm text-fg-subtle cursor-pointer">Details</summary>
									<p class="mt-1 break-words font-mono text-xs text-fg-muted">
										{group.latest.safe_technical_detail}
									</p>
								</details>
							{/if}
						</details>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>
