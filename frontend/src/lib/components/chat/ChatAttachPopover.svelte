<script lang="ts">
	// Vision-image attach surface opened from ChatInput's "Add" button: current-
	// form thumbnails, the shared MediaLoaderField (upload/library/paste/history
	// — whatever it already offers, nothing invented here), reuse-last-image,
	// and the auto-attach-next-message checkbox. Markup ported verbatim from
	// the mock's .media-popover (chat-rework BRIEF2) — absolutely positioned
	// inside .composer-wrap (its CSS), a plain sibling of .composer, no portal.
	import { onMount } from 'svelte';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import type { FormImageEntry } from '$lib/chat/formMedia';

	export let triggerEl: HTMLElement | undefined = undefined;
	export let onClose: () => void;

	export let formImageEntries: FormImageEntry[] = [];
	export let selectedDurablePath: string | null = null;
	export let onSelectFormImage: (entry: FormImageEntry) => void = () => {};
	export let selectedImageData: unknown = null;
	export let onMediaLoaderChange: (value: unknown) => void = () => {};
	export let lastGeneratedImage: { url: string; name: string; generatedAt?: string } | null = null;
	export let onAttachLastImage: () => void = () => {};
	export let alwaysAttachLastImage = false;
	export let onToggleAttachImage: (() => void) | undefined = undefined;

	let rootEl: HTMLDivElement;

	function isSelected(entry: FormImageEntry): boolean {
		return (entry.media.relative_path || entry.media.path) === selectedDurablePath;
	}

	function pickFormImage(entry: FormImageEntry) {
		onSelectFormImage(entry);
		onClose();
	}

	function attachAgain() {
		onAttachLastImage();
		onClose();
	}

	function handleFieldChange(_fieldName: string, value: unknown) {
		onMediaLoaderChange(value);
	}

	function handleOutsidePointerDown(e: PointerEvent) {
		const target = e.target as Node;
		if (rootEl?.contains(target) || triggerEl?.contains(target)) return;
		// MediaLoaderField opens its own portaled modals (history/library/
		// preview, all BaseModal.svelte) OUTSIDE this popover's DOM subtree even
		// though they're mounted as Svelte descendants of it - a click inside one
		// would otherwise register as "outside" and tear this popover (and the
		// modal with it) down mid-pick. GlobalChatPanel's own shell also carries
		// aria-modal="true" and is an ancestor of every click target inside the
		// open chat panel, so it must be excluded here or this guard would
		// swallow every outside click and this popover would never close.
		const nestedModal = target instanceof Element ? target.closest('[aria-modal="true"]') : null;
		if (nestedModal && nestedModal.getAttribute('aria-label') !== 'AI chat') return;
		onClose();
	}

	function handleOutsideKeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape') return;
		// A nested BaseModal (history/library/preview) owns this Escape itself,
		// via its own capture-phase window listener that runs first - by the
		// time it bubbles here focus is still inside the modal's dialog, so
		// don't also tear down this popover underneath it. GlobalChatPanel's
		// own shell also carries aria-modal="true" and is an ancestor of
		// EVERYTHING in the open chat panel (including this popover's trigger),
		// so it must be excluded here or this guard would swallow every Escape
		// press and this popover would never close on its own.
		const nestedModal = e.target instanceof Element ? e.target.closest('[aria-modal="true"]') : null;
		if (nestedModal && nestedModal.getAttribute('aria-label') !== 'AI chat') return;
		onClose();
		// Keeps GlobalChatPanel's <svelte:window on:keydown> from also closing
		// the whole chat panel on this same Escape press (see ChatInput's Tools
		// dropdown for the same reasoning).
		e.stopPropagation();
	}

	onMount(() => {
		document.addEventListener('pointerdown', handleOutsidePointerDown, true);
		document.addEventListener('keydown', handleOutsideKeydown);
		return () => {
			document.removeEventListener('pointerdown', handleOutsidePointerDown, true);
			document.removeEventListener('keydown', handleOutsideKeydown);
		};
	});
</script>

<div
	class="media-popover"
	bind:this={rootEl}
	role="dialog"
	aria-label="Attach an image"
	data-testid="chat-attach-popover"
>
	<div class="media-head">
		<span class="media-head-icon"><svg class="icon"><use href="#i-image" /></svg></span>
		<div class="media-head-copy">
			<strong>Attach an image</strong>
			<span>One image can be sent with the next message</span>
		</div>
		<span class="capability-badge">VISION</span>
		<button class="tiny-button" aria-label="Close image picker" title="Close" on:click={onClose}>
			<svg class="icon"><use href="#i-close" /></svg>
		</button>
	</div>

	<div class="media-body">
		{#if formImageEntries.length > 0}
			<div class="media-section-label">From current form</div>
			<div class="form-images">
				{#each formImageEntries as entry (entry.key)}
					<button
						type="button"
						class="form-image"
						class:selected={isSelected(entry)}
						title={entry.label}
						aria-label={entry.label}
						on:click={() => pickFormImage(entry)}
					>
						<img src={entry.url} alt={entry.label} />
					</button>
				{/each}
			</div>
		{/if}

		<div class="media-loader-field">
			<MediaLoaderField
				name="vision_image"
				value={selectedImageData}
				onChange={handleFieldChange}
				config={{ title: 'Reference image', accept: 'image/*' }}
				compact
				compactFullWidth
			/>
		</div>

		{#if lastGeneratedImage}
			<div class="last-image-row">
				<img src={lastGeneratedImage.url} alt="Last generated" />
				<div class="last-image-copy">
					<strong>Use last generated image</strong>
					<span
						>{lastGeneratedImage.name}{#if lastGeneratedImage.generatedAt}
							· {lastGeneratedImage.generatedAt}{/if}</span
					>
				</div>
				<button type="button" class="reuse-button" on:click={attachAgain}>Attach again</button>
			</div>
		{/if}
	</div>

	{#if onToggleAttachImage}
		<label class="auto-attach-row">
			<input type="checkbox" checked={alwaysAttachLastImage} on:change={() => onToggleAttachImage?.()} />
			<span>Auto-attach the last generated image to your next message</span>
			<small>ONCE WHEN NEW</small>
		</label>
	{/if}
</div>
