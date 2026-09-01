/**
 * Picks the clipboard item that carries an image out of a paste event's
 * items, if any. Same MIME sniff MediaLoaderField's own paste handler uses
 * (`type.indexOf('image') !== -1`), so an image the chat composer picks up
 * is exactly one the field would have accepted through its own paste path.
 *
 * Takes `ArrayLike` rather than `DataTransferItemList` so it's testable
 * with plain arrays - no DOM clipboard mocking required.
 */
export function findImageItemIndex(items: ArrayLike<{ type: string }>): number {
	for (let i = 0; i < items.length; i++) {
		if (items[i].type.indexOf('image') !== -1) return i;
	}
	return -1;
}
