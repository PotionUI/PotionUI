<script>
  // Reference implementation for a plugin-provided field type (roadmap A4).
  // Props follow the same {name, config, value, onChange, host} contract as
  // every core field component resolved by FormField.svelte - `host` is the
  // window.__potionui bridge object, unused here but available for plugins
  // that need host primitives (Modal, Icon, formReactions, etc.).
  export let name = null;
  export let config = {};
  export let value = 0;
  export let onChange = () => {};
  export let host = undefined;

  $: label = config.title || name || '';
  $: description = config.description || '';
  $: max = config.configuration?.max_stars || 5;
  $: rating = typeof value === 'number' ? value : 0;

  function setRating(star) {
    if (name) onChange(name, star);
  }
</script>

<div class="example-stars-field">
  {#if label}
    <label class="example-stars-label">{label}</label>
  {/if}
  <div class="example-stars-row" role="radiogroup" aria-label={label || 'Rating'}>
    {#each Array(max) as _, i}
      {@const star = i + 1}
      <button
        type="button"
        class="example-star"
        class:filled={star <= rating}
        on:click={() => setRating(star)}
        aria-checked={star === rating}
        role="radio"
        title="{star} star{star !== 1 ? 's' : ''}"
      >
        &#9733;
      </button>
    {/each}
  </div>
  {#if description}
    <p class="example-stars-desc">{description}</p>
  {/if}
</div>

<style>
  .example-stars-field {
    padding: 0.5rem 0;
  }
  .example-stars-label {
    display: block;
    font-size: 0.75rem;
    font-weight: 500;
    margin-bottom: 0.375rem;
    opacity: 0.8;
  }
  .example-stars-row {
    display: flex;
    gap: 0.25rem;
  }
  .example-star {
    background: none;
    border: none;
    font-size: 1.5rem;
    line-height: 1;
    cursor: pointer;
    opacity: 0.3;
    padding: 0;
    transition: opacity 0.15s ease, transform 0.1s ease;
  }
  .example-star:hover {
    transform: scale(1.15);
  }
  .example-star.filled {
    opacity: 1;
  }
  .example-stars-desc {
    font-size: 0.75rem;
    opacity: 0.6;
    margin-top: 0.25rem;
  }
</style>
