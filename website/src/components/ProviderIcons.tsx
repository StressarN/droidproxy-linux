import { Claude, Codex, Gemini } from '@lobehub/icons';

export default function ProviderIcons() {
  return (
    <div className="flex flex-wrap items-center justify-center gap-8">
      <div className="flex flex-col items-center gap-2">
        <Claude.Color size={48} />
        <span className="text-sm font-medium text-apple-gray-600 dark:text-apple-gray-300">Claude</span>
      </div>
      <div className="flex flex-col items-center gap-2">
        <Codex.Color size={48} />
        <span className="text-sm font-medium text-apple-gray-600 dark:text-apple-gray-300">Codex</span>
      </div>
      <div className="flex flex-col items-center gap-2">
        <Gemini.Color size={48} />
        <span className="text-sm font-medium text-apple-gray-600 dark:text-apple-gray-300">Gemini</span>
      </div>
    </div>
  )
}