<script lang="ts">
	import Icon from '../Icon.svelte';

	export let url: string | null | undefined = undefined;
	export let kind: string | null | undefined = undefined;
	export let name: string = '';
	export let className: string = '';
	export let size: string | number | undefined = undefined;
	export let rounded: boolean = true;
	export let iconClassName: string = 'w-4 h-4';

	let failed = false;

	function detectVideoFromUrl(src: string): boolean {
		return /\.(mp4|webm|mov)(\?.*)?$/i.test(src);
	}

	$: if (url) failed = false;

	$: resolvedKind =
		kind === 'image' || kind === 'video' || kind === 'audio'
			? kind
			: url && detectVideoFromUrl(url)
				? 'video'
				: 'image';

	$: showImage = !failed && !!url && resolvedKind === 'image';
	$: showVideo = !failed && !!url && resolvedKind === 'video';

	$: iconName = resolvedKind === 'video' ? 'video' : resolvedKind === 'audio' ? 'audio' : 'image';

	$: sizeStyle =
		size === undefined ? undefined : `width:${typeof size === 'number' ? `${size}px` : size};height:${typeof size === 'number' ? `${size}px` : size};`;

	function handleError() {
		failed = true;
	}
</script>

<span class="media-thumb {rounded ? 'rounded' : ''} {className}" style={sizeStyle}>
	{#if showImage}
		<img src={url} alt={name} loading="lazy" on:error={handleError} />
	{:else if showVideo}
		<video src={url} muted playsinline preload="metadata" on:error={handleError}>
			<track kind="captions" />
		</video>
		<span class="media-thumb-badge"><Icon name="play" className="icon" /></span>
	{:else}
		<Icon name={iconName} className={iconClassName} />
	{/if}
</span>

<style>
	.media-thumb {
		position: relative;
		display: flex;
		flex: none;
		align-items: center;
		justify-content: center;
		overflow: hidden;
	}

	.media-thumb img,
	.media-thumb video {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}

	.media-thumb-badge {
		position: absolute;
		right: 2px;
		bottom: 2px;
		width: 14px;
		height: 14px;
		display: grid;
		place-items: center;
		background: rgb(0 0 0 / 0.55);
		border-radius: 3px;
		color: rgb(var(--fg));
	}

	.media-thumb-badge :global(.icon) {
		width: 8px;
		height: 8px;
	}
</style>
