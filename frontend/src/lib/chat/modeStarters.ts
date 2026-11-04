export interface ChatStarter {
	title: string;
	sub: string;
	prompt: string;
	icon: string;
}

const GENERATION: ChatStarter[] = [
	{
		title: 'Explore art directions',
		sub: 'Three distinct approaches from the current image',
		prompt: 'Give me three art directions for the current image.',
		icon: 'chat-sparkles'
	},
	{
		title: 'Review my settings',
		sub: 'Find quality, speed, or composition improvements',
		prompt: 'Review my current settings and suggest improvements.',
		icon: 'chat-sliders'
	},
	{
		title: 'Refine this prompt',
		sub: 'Improve composition, detail, and lighting',
		prompt: 'Rewrite my prompt with stronger composition and lighting.',
		icon: 'chat-wand'
	}
];

const HISTORY: ChatStarter[] = [
	{
		title: 'Find generations',
		sub: 'Search past work by subject, model, or date',
		prompt: 'Find my favorite generations from the past week.',
		icon: 'chat-search'
	},
	{
		title: 'Review recent work',
		sub: 'See what you made and which settings worked',
		prompt: 'Summarize my recent generations and which settings gave the best results.',
		icon: 'chat-image'
	},
	{
		title: 'Tag and organize',
		sub: 'Tidy untagged generations into groups',
		prompt: 'Suggest tags and collections for my untagged generations.',
		icon: 'chat-library'
	}
];

const PROMPTS: ChatStarter[] = [
	{
		title: 'Find a saved prompt',
		sub: 'Search your library by subject or style',
		prompt: 'Search my saved prompts for portrait ideas.',
		icon: 'chat-search'
	},
	{
		title: 'Save a new prompt',
		sub: 'Turn an idea into a reusable library entry',
		prompt: 'Write a detailed prompt for a candlelit potion shop and save it to my library.',
		icon: 'chat-plus'
	},
	{
		title: 'Improve a saved prompt',
		sub: 'Sharpen composition, detail, and lighting',
		prompt: 'Pick one of my saved prompts and rewrite it with stronger composition and lighting.',
		icon: 'chat-wand'
	}
];

const MODELS: ChatStarter[] = [
	{
		title: 'What do I have installed?',
		sub: 'A quick tour of your checkpoints and LoRAs',
		prompt: 'List my installed checkpoints and what each one is best at.',
		icon: 'chat-library'
	},
	{
		title: 'Look up a model',
		sub: 'Type, base model, and where it is used',
		prompt: 'Tell me about my newest model: its type, base model, and recommended settings.',
		icon: 'chat-info'
	},
	{
		title: 'Find the right model',
		sub: 'Match a model to what you want to make',
		prompt: 'Which of my models is best for photorealistic portraits?',
		icon: 'chat-search'
	}
];

const PHRASEBOOK: ChatStarter[] = [
	{
		title: 'Browse categories',
		sub: 'See what your phrasebook already covers',
		prompt: 'List my phrasebook categories and how many values each one has.',
		icon: 'chat-library'
	},
	{
		title: 'Add values',
		sub: 'Grow a category with fresh options',
		prompt: 'Suggest ten new values for one of my phrasebook categories.',
		icon: 'chat-plus'
	},
	{
		title: 'Tidy a category',
		sub: 'Find duplicates and weak entries',
		prompt: 'Review one of my phrasebook categories for duplicates and weak values.',
		icon: 'chat-sliders'
	}
];

const ELIXIRS: ChatStarter[] = [
	{
		title: 'Explain this elixir',
		sub: 'What each step does, left to right',
		prompt: 'Walk me through this elixir step by step.',
		icon: 'chat-info'
	},
	{
		title: 'Add a step',
		sub: 'Extend the chain with another preset',
		prompt: 'Add an upscale step after the last step of this elixir.',
		icon: 'chat-plus'
	},
	{
		title: 'Help me choose',
		sub: 'Pick the outputs worth keeping',
		prompt: 'Look at the outputs waiting for a choice and pick the best ones.',
		icon: 'chat-image'
	}
];

const GENERIC: ChatStarter[] = [
	{
		title: 'What can you do here?',
		sub: 'A quick overview of this assistant',
		prompt: 'What can you help me with on this page?',
		icon: 'chat-info'
	},
	{
		title: 'Summarize this page',
		sub: 'What is in front of you right now',
		prompt: 'Summarize what I am looking at on this page.',
		icon: 'chat-search'
	},
	{
		title: 'Suggest a next step',
		sub: 'Where to go from here',
		prompt: 'Suggest a good next step for me.',
		icon: 'chat-sparkles'
	}
];

const BY_MODE: Record<string, ChatStarter[]> = {
	generation: GENERATION,
	history: HISTORY,
	prompts: PROMPTS,
	models: MODELS,
	phrasebook: PHRASEBOOK,
	elixirs: ELIXIRS
};

export function startersForMode(modeId: string | null | undefined): ChatStarter[] {
	return (modeId && BY_MODE[modeId]) || GENERIC;
}

export interface ChatIntro {
	heading: string;
	body: string;
}

const DEFAULT_INTRO: ChatIntro = {
	heading: 'What are we making?',
	body: 'I can see your active workspace and help shape prompts, settings, and creative direction.'
};

const INTRO_BY_MODE: Record<string, ChatIntro> = {
	history: {
		heading: 'What are we looking for?',
		body: 'I can find your past generations, sort them into collections and tags, and edit files for you.'
	},
	prompts: {
		heading: 'What should we write?',
		body: 'I can search your saved prompts, write new ones and keep your library tidy.'
	},
	models: {
		heading: 'Which model are you after?',
		body: 'I can look up your installed models, what each one does and its trigger words.'
	},
	phrasebook: {
		heading: 'What are we collecting?',
		body: 'I can browse your phrasebook categories and help you add to them and clean up their values.'
	},
	elixirs: {
		heading: 'What should this elixir do?',
		body: 'I can change its steps and settings and pick outputs at a choice. You press Brew and Continue.'
	}
};

export function introForMode(modeId: string | null | undefined): ChatIntro {
	return (modeId && INTRO_BY_MODE[modeId]) || DEFAULT_INTRO;
}
