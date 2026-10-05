"use client";
import { usePathname } from "next/navigation";
import Icon from "./Icon";

const LINKS = [
  { href: "/", label: "Cases", icon: "folder", on: (p: string) => p === "/" || p.startsWith("/cases") },
  { href: "/admin/rules", label: "Rules", icon: "book", on: (p: string) => p.startsWith("/admin/rules") },
] as const;

export default function TopNav() {
  const path = usePathname() ?? "/";
  return (
    <header className="topnav">
      <a href="/" className="brand">
        <span className="brand-mark"><Icon name="landmark" size={17} /></span>
        <span className="brand-text">
          Revenue Case Intelligence
          <small>Revenue Department</small>
        </span>
      </a>
      <nav className="topnav-links" aria-label="Main">
        {LINKS.map((l) => (
          <a key={l.href} href={l.href} className={l.on(path) ? "on" : undefined} aria-current={l.on(path) ? "page" : undefined}>
            <Icon name={l.icon} size={15} /> {l.label}
          </a>
        ))}
      </nav>
    </header>
  );
}
