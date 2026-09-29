<script lang="ts">
	import { onMount, onDestroy, untrack } from 'svelte';
	import { DetailSection } from '$lib/components/detail';
	import { Alert, Badge, Button, SegmentedControl } from '$lib/components/ui';
	import * as adminApi from '$lib/services/admin-api';
	import { logger } from '$lib/utils/logger';
	import {
		CONTENT_POLICY_OPTIONS,
		backfillPercent,
		isContentPolicy,
		taggerNotice,
		type ContentPolicy,
		type ContentSafetyStatus
	} from '$lib/contentSafety/policy';
	import { findBannedWord, formatBannedWords, parseBannedWords } from '$lib/contentSafety/bannedWords';
	import { createModelFetchController, isFetchDisabled, modelNameLookup } from './modelFetch.svelte';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	const fetch = createModelFetchController(['media_tagger'], (kind) => modelNameLookup(settings)(kind));

	let status = $state<ContentSafetyStatus | null>(null);
	let statusError = $state(false);
	let bannedText = $state(untrack(() => formatBannedWords(settings.content_banned_words)));
	let testPrompt = $state('');
	let poll: ReturnType<typeof setInterval> | null = null;

	const policy = $derived<ContentPolicy>(
		isContentPolicy(settings.content_policy_nsfw) ? settings.content_policy_nsfw : 'allowed'
	);
	const taggerState = $derived(fetch.state.media_tagger);
	const taggerPresent = $derived((status?.tagger.present ?? false) || taggerState.status === 'ready');
	const taggerDownloading = $derived(
		(status?.tagger.downloading ?? false) ||
			taggerState.status === 'queued' ||
			taggerState.status === 'downloading'
	);
	const notice = $derived(
		status || taggerState.status !== 'checking'
			? taggerNotice(policy, { present: taggerPresent, downloading: taggerDownloading })
			: null
	);
	const bannedWords = $derived(parseBannedWords(bannedText));
	const testMatch = $derived(testPrompt.trim() ? findBannedWord(testPrompt, bannedWords) : null);
	const backfill = $derived(status?.backfill ?? null);

	$effect(() => {
		const stored = JSON.stringify(settings.content_banned_words ?? []);
		untrack(() => {
			if (stored !== JSON.stringify(parseBannedWords(bannedText))) {
				bannedText = formatBannedWords(settings.content_banned_words);
			}
		});
	});

	async function loadStatus() {
		try {
			const res = await adminApi.getContentSafetyStatus();
			if (res.success && res.data) {
				status = res.data;
				statusError = false;
			} else {
				statusError = true;
			}
		} catch (err) {
			statusError = true;
			logger.error('Failed to load content safety status:', err);
		}
	}

	$effect(() => {
		if (taggerState.status === 'ready') void loadStatus();
	});

	onMount(() => {
		void loadStatus();
		poll = setInterval(() => {
			if (status?.backfill.running || status?.tagger.downloading) void loadStatus();
		}, 5000);
	});

	onDestroy(() => {
		if (poll) clearInterval(poll);
	});

	function onNumberInput(key: string, e: Event) {
		const value = (e.currentTarget as HTMLInputElement).value;
		onSettingChange(key, value === '' ? null : Number(value));
	}

	function onBannedInput(e: Event) {
		bannedText = (e.currentTarget as HTMLTextAreaElement).value;
		onSettingChange('content_banned_words', parseBannedWords(bannedText));
	}

	async function downloadAndEnable() {
		onSettingChange('media_tagger_auto_download', true);
		await fetch.fetchModel('media_tagger');
	}
</script>

<DetailSection label="Content Safety" padded={false}>
	<div class="px-4 sm:px-5 divide-y divide-line">
		<div class="py-4 space-y-3">
			<div class="flex items-start justify-between gap-6">
				<div>
					<span id="content-policy-label" class="block text-sm font-medium text-fg mb-1">NSFW content</span>
					<p class="text-sm text-fg-muted">
						Allowed skips all checks. Blur flags NSFW outputs and blurs them. Blocked never saves or
						delivers them.
					</p>
				</div>
				<SegmentedControl
					variant="toggle"
					ariaLabel="NSFW content policy"
					items={CONTENT_POLICY_OPTIONS}
					selected={policy}
					onSelect={(id) => onSettingChange('content_policy_nsfw', id)}
				/>
			</div>
			{#if notice}
				<Alert variant={notice.tone} density="compact" icon>{notice.text}</Alert>
			{/if}
		</div>

		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<span class="block text-sm font-medium text-fg mb-1">Rating model</span>
				<p class="text-sm text-fg-muted">
					A local image-rating model checks every output. Blur and Blocked need it.
					{#if status?.tagger.device}
						Runs on <span class="font-mono">{status.tagger.device}</span>.
					{/if}
				</p>
			</div>
			<div class="flex items-center gap-3 flex-shrink-0" data-tagger-status>
				{#if taggerPresent}
					<Badge variant="success">Ready</Badge>
				{:else if taggerDownloading}
					<span class="font-mono text-xs tabular-nums text-fg-muted">
						{taggerState.status === 'queued' ? 'queued' : `${Math.round(taggerState.progress * 100)}%`}
					</span>
					<Badge variant="info">Downloading</Badge>
				{:else if taggerState.status === 'failed'}
					<Badge variant="danger">Failed</Badge>
				{:else if statusError}
					<Badge variant="warning">Status unavailable</Badge>
				{:else}
					<Badge variant="warning">Not downloaded</Badge>
				{/if}
				{#if !taggerPresent}
					<Button
						size="sm"
						variant="secondary"
						disabled={isFetchDisabled(taggerState) || taggerDownloading}
						loading={taggerState.status === 'checking' || taggerState.status === 'queued'}
						onclick={downloadAndEnable}
					>
						Download and enable
					</Button>
				{/if}
			</div>
		</div>

		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="media-nsfw-blur-threshold" class="block text-sm font-medium text-fg mb-1">
					Content rating threshold
				</label>
				<p class="text-sm text-fg-muted">
					An output counts as NSFW when its questionable + explicit rating reaches this value
				</p>
			</div>
			<input
				id="media-nsfw-blur-threshold"
				type="number"
				min="0"
				max="1"
				step="0.05"
				class="input w-24 flex-shrink-0 font-mono tabular-nums text-sm"
				value={settings.media_nsfw_blur_threshold ?? 0.6}
				oninput={(e) => onNumberInput('media_nsfw_blur_threshold', e)}
			/>
		</div>

		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="content-video-sample-frames" class="block text-sm font-medium text-fg mb-1">
					Video sample frames
				</label>
				<p class="text-sm text-fg-muted">
					Frames rated per video, spread evenly across it. The highest rating counts. Maximum 8.
				</p>
			</div>
			<input
				id="content-video-sample-frames"
				type="number"
				min="1"
				max="8"
				step="1"
				class="input w-24 flex-shrink-0 font-mono tabular-nums text-sm"
				value={settings.content_video_sample_frames ?? 5}
				oninput={(e) => onNumberInput('content_video_sample_frames', e)}
			/>
		</div>

		<div class="py-4 space-y-3">
			<div>
				<label for="content-banned-words" class="block text-sm font-medium text-fg mb-1">Banned words</label>
				<p class="text-sm text-fg-muted">
					Prompts that contain any of these words are refused. One word or phrase per line; use
					<span class="font-mono">*</span> as a wildcard (<span class="font-mono">nud*</span> matches nude, nudity).
					Case and accents are ignored, and only whole words match.
				</p>
			</div>
			<textarea
				id="content-banned-words"
				class="input w-full font-mono text-sm"
				rows="6"
				spellcheck="false"
				autocomplete="off"
				value={bannedText}
				oninput={onBannedInput}
			></textarea>
			<p class="text-sm text-fg-subtle">
				<span class="font-mono tabular-nums">{bannedWords.length}</span>
				{bannedWords.length === 1 ? 'entry' : 'entries'}
			</p>

			<div class="space-y-2">
				<label for="content-banned-test" class="block text-sm font-medium text-fg">
					Would this prompt be refused?
				</label>
				<div class="flex items-center gap-3">
					<input
						id="content-banned-test"
						type="text"
						class="input flex-1 min-w-0 text-sm"
						placeholder="Type a prompt to test against the list above"
						bind:value={testPrompt}
					/>
					<div class="flex-shrink-0 min-w-[9rem]" data-banned-test-result>
						{#if !testPrompt.trim()}
							<span class="text-sm text-fg-subtle">Nothing to test</span>
						{:else if testMatch}
							<Badge variant="danger">Refused: <span class="font-mono">{testMatch}</span></Badge>
						{:else}
							<Badge variant="success">Would pass</Badge>
						{/if}
					</div>
				</div>
			</div>
		</div>

		{#if backfill && backfill.total > 0}
			<div class="py-4 space-y-2" data-backfill>
				<div class="flex items-center justify-between gap-6">
					<div>
						<span class="block text-sm font-medium text-fg mb-1">Rating existing media</span>
						<p class="text-sm text-fg-muted">
							Restricted accounts only see media that has been rated.
						</p>
					</div>
					<div class="flex items-center gap-3 flex-shrink-0">
						<span class="font-mono text-sm tabular-nums text-fg-muted">
							{backfill.rated} / {backfill.total}
						</span>
						{#if backfill.running}
							<Badge variant="info">Running</Badge>
						{:else if backfill.rated >= backfill.total}
							<Badge variant="success">Complete</Badge>
						{:else}
							<Badge variant="warning">Paused</Badge>
						{/if}
					</div>
				</div>
				<div class="h-1.5 w-full overflow-hidden rounded bg-surface-3" role="progressbar" aria-label="Rating progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow={backfillPercent(backfill)}>
					<div class="h-full bg-signal-solid transition-[width]" style="width: {backfillPercent(backfill)}%"></div>
				</div>
			</div>
		{/if}
	</div>
</DetailSection>
