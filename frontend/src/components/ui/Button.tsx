import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  icon?: ReactNode;
}

export function Button({
  children,
  className,
  variant = 'primary',
  size = 'md',
  icon,
  ...props
}: ButtonProps) {
  const variants = {
    primary:
      'bg-cyan-500 text-navy-950 hover:bg-cyan-400 shadow-glow font-semibold disabled:opacity-50',
    secondary:
      'bg-navy-700/50 text-slate-200 hover:bg-navy-600/50 border border-navy-600/50',
    ghost: 'text-slate-300 hover:bg-navy-700/40 hover:text-slate-100',
    danger: 'bg-rose-500/90 text-white hover:bg-rose-500 font-semibold',
  };
  const sizes = {
    sm: 'px-3 py-1.5 text-xs',
    md: 'px-4 py-2 text-sm',
    lg: 'px-5 py-2.5 text-base',
  };
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-lg transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-cyan-400/40 disabled:cursor-not-allowed',
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    >
      {icon}
      {children}
    </button>
  );
}
