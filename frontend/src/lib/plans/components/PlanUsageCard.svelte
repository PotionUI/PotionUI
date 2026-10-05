<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { Button, Card, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { getMyStorageBreakdown, type StorageGroup } from '../meApi';
	import { limits, limitsLoaded, limitsMeta, nowTick, refreshLimits, storageUsed } from '../store';
	import { formatBytesPlain } from '../limitView';
	import UsageRows from './UsageRows.svelte';

	let groups = $state<StorageGroup[]>([]);

	const groupIcons: Record<string, string> = {
		image: 'image',
		video: 'video',
		audio: 'audio',
		mesh: 'cube',
		upload: 'document'
	};

	onMount(() => {
		void refreshLimits();
		getMyStorageBreakdown().then(
			(result) => (groups = result),
			() => (groups = [])
		);
		if (location.hash === '#plan') document.getElementById('plan')?.scrollIntoView();
	});

	let hasStorage = $derived($limits.some((row) => row.format === 'bytes' && !row.perItem));
	let source = $derived(
		$limitsMeta?.source === 'group' && $limitsMeta.groupName ? `from the group ${$limitsMeta.groupName}` : null
	);
</script>

<div id="plan" class="scroll-mt-6">
	<Card>
		<h2 class="label mb-3">Plan &amp; usage</h2>
		{#if !$limitsLoaded}
			<Spinner />
		{:else}
			{#if $limitsMeta?.planName && $limits.length > 0}
				<div class="flex items-baseline justify-between gap-3 pb-1">
					<p class="text-sm text-fg">
						<span class="font-semibold">{$limitsMeta.planName}</span>
						{#if source}<span class="text-fg-subtle">{source}</span>{/if}
					</p>
					{#if $limitsMeta.timezone}
						<p class="text-xs text-fg-subtle">Day resets at 00:00 {$limitsMeta.timezone}</p>
					{/if}
				</div>
			{/if}
			<UsageRows rows={$limits} now={$nowTick} storageBytes={$storageUsed} />
			{#if hasStorage && groups.length > 0}
				<h3 class="label mt-5 mb-2">What is using storage</h3>
				<ul class="divide-y divide-line" data-storage-breakdown>
					{#each groups as group (group.key)}
						<li class="flex items-center gap-3 py-2">
							<Icon name={groupIcons[group.key] ?? 'document'} className="w-4 h-4 shrink-0 text-fg-subtle" strokeWidth={1.5} />
							<p class="text-sm text-fg flex-1 min-w-0 truncate">{group.label}</p>
							<p class="text-xs text-fg-subtle font-mono tabular-nums">{group.files} files</p>
							<p class="w-20 text-right font-mono tabular-nums text-sm text-fg">{formatBytesPlain(group.bytes)}</p>
						</li>
					{/each}
				</ul>
				<div class="mt-3 flex items-center gap-3">
					<Button variant="secondary" size="sm" onclick={() => goto('/history?sort=largest')}>Review largest generations</Button>
					<p class="text-xs text-fg-subtle">Thumbnails are not counted. Nothing is deleted for you.</p>
				</div>
			{/if}
		{/if}
	</Card>
</div>
