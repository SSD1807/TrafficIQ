import { cn } from '@/lib/utils';

interface SelectProps {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  className?: string;
  id?: string;
}

export function Select({ label, value, onChange, options, className, id }: SelectProps) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {label && (
        <label htmlFor={id} className="text-xs font-medium text-slate-400">
          {label}
        </label>
      )}
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-navy-600/50 bg-navy-800/60 px-3 py-2 text-sm text-slate-200 outline-none transition-colors focus:border-cyan-500/50 focus:ring-1 focus:ring-cyan-500/30"
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value} className="bg-navy-900 text-slate-200">
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  );
}

interface InputProps {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  id?: string;
  type?: string;
}

export function Input({ label, value, onChange, placeholder, className, id, type = 'text' }: InputProps) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {label && (
        <label htmlFor={id} className="text-xs font-medium text-slate-400">
          {label}
        </label>
      )}
      <input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-navy-600/50 bg-navy-800/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 outline-none transition-colors focus:border-cyan-500/50 focus:ring-1 focus:ring-cyan-500/30"
      />
    </div>
  );
}
