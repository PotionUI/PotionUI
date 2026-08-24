/**
 * Tutorial section interface for help content
 * Used in TutorialModal component
 */
export interface TutorialSection {
	/** Section title */
	title: string;
	/** Section content (supports markdown: **bold**, *italic*, `code`) */
	content: string;
	/** Optional icon name ('info', 'lightbulb', 'warning', 'check', 'star') */
	icon?: string;
	/** Optional variant for styling ('default', 'tip', 'warning') */
	variant?: 'default' | 'tip' | 'warning';
}
