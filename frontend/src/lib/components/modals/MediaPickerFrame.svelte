<script lang="ts">
	import portal from '$lib/actions/portal';
	import BaseModal from './BaseModal.svelte';

	// The portal + BaseModal shell shared by every "pick a file from an
	// existing library" modal (generation history, uploads, …). Filters,
	// cards, deletion and the picked-payload shape are the caller's own -
	// this only owns the wrapper and forwards header/body/footer through.
	export let isOpen: boolean;
	export let onClose: () => void;
	export let title: string;
	export let subtitle: string = '';
	export let sizeClass: string = 'md:max-w-6xl md:w-full';
</script>

{#if isOpen}
	<div use:portal>
		<BaseModal isOpen={true} {title} {subtitle} {sizeClass} on:close={onClose}>
			<!-- Fragments cannot be wrapped in {#if}, so both are always forwarded:
			     BaseModal therefore always sees a header/footer slot. Harmless while
			     every caller supplies both; a footer-less caller would need a
			     hideFooter prop on BaseModal rather than a conditional here. -->
			<svelte:fragment slot="header">
				<slot name="header" />
			</svelte:fragment>

			<slot />

			<svelte:fragment slot="footer">
				<slot name="footer" />
			</svelte:fragment>
		</BaseModal>
	</div>
{/if}
