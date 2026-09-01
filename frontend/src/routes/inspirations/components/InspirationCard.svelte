<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { placeholderTint } from '$lib/utils/placeholderTint';
	import type { InspirationDto } from '$lib/services/api/inspirations';
	import {
		inspirationPrimaryMedia,
		inspirationIsVideo,
		inspirationDisplayTitle,
		inspirationAuthorInitial
	} from '$lib/inspirations/inspirationCardMeta';

	export let item: InspirationDto;
	export let onOpen: (item: InspirationDto) => void;
	/**
	 * Justified-gallery box in px (native aspect ratio, from `justifiedLayout`).
	 * When null the card falls back to a fixed aspect-[4/3] tile.
	 */
	export let tile: { width: number; height: number } | null = null;

	$: media = inspirationPrimaryMedia(item);
	$: isVideo = inspirationIsVideo(media);
	$: title = inspirationDisplayTitle(item);
	$: authorInitial = inspirationAuthorInitial(item.author);
</script>

<button
	type="button"
	class="group text-left w-full rounded-lg overflow-hidden border border-line-strong hover:border-line-hover bg-surface-1 transition-colors duration-100"
	style={tile ? `width: ${tile.width}px` : undefined}
	on:click={() => onOpen(item)}
>
	<div
		class="relative bg-black overflow-hidden {tile ? '' : 'aspect-[4/3]'}"
		style={tile ? `height: ${tile.height}px` : undefined}
	>
		{#if media}
			{#if isVideo}
				<video
					src={media.url}
					class="w-full h-full {tile ? 'object-contain' : 'object-cover'}"
					muted
					playsinline
					preload="metadata"
				>
					<track kind="captions" />
				</video>
			{:else}
				<img
					src={media.url}
					alt={title}
					class="w-full h-full {tile ? 'object-contain' : 'object-cover'}"
					loading="lazy"
				/>
			{/if}
		{:else}
			<div class="w-full h-full flex items-center justify-center" style={placeholderTint(title)}>
				<Icon name="photo" className="w-8 h-8 text-fg-subtle" strokeWidth={1.5} />
			</div>
		{/if}

		{#if isVideo}
			<div
				class="absolute bottom-1.5 left-1.5 z-10 flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/70 backdrop-blur-sm text-fg font-mono text-2xs tracking-[0.07em]"
			>
				<Icon name="video" className="h-2.5 w-2.5" />
			</div>
		{/if}

		{#if item.media.length > 1}
			<div
				class="absolute top-1.5 right-1.5 z-10 px-1.5 py-0.5 rounded bg-black/70 backdrop-blur-sm text-fg font-mono tabular-nums text-2xs"
			>
				+{item.media.length - 1}
			</div>
		{/if}

		{#if item.saved_by_me}
			<div class="absolute top-1.5 left-1.5 z-10 p-1 rounded bg-black/70 backdrop-blur-sm text-signal">
				<Icon name="save" className="h-3 w-3" />
			</div>
		{/if}
	</div>

	<div class="p-2.5 space-y-2">
		<h3 class="text-xs font-medium text-fg truncate" title={title}>{title}</h3>

		<div class="flex items-center gap-1.5 min-w-0">
			{#if item.author.avatar_url}
				<img
					src={item.author.avatar_url}
					alt=""
					class="w-4 h-4 rounded-full flex-shrink-0 object-cover"
				/>
			{:else}
				<span
					class="w-4 h-4 rounded-full flex-shrink-0 bg-surface-3 text-fg-subtle flex items-center justify-center text-2xs font-medium"
				>
					{authorInitial}
				</span>
			{/if}
			<span class="text-2xs text-fg-muted truncate">{item.author.username}</span>
		</div>

		<div class="flex items-center justify-between font-mono tabular-nums text-2xs text-fg-subtle">
			<div class="flex items-center gap-2">
				<span class="flex items-center gap-1">
					<Icon name="save" className="w-3 h-3" />
					{item.save_count}
				</span>
				<span class="flex items-center gap-1">
					<Icon name="chat" className="w-3 h-3" />
					{item.comment_count}
				</span>
			</div>
			<span class="whitespace-nowrap">{timeAgo(item.created_at)}</span>
		</div>
	</div>
</button>
