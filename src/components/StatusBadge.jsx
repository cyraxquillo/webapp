import { CheckCircle2, Info, MapPin, XCircle } from 'lucide-react'

const statusRules = [
  {
    tone: 'success',
    icon: CheckCircle2,
    pattern: /^(inside|present|excellent|good|high performer|average|active|on time|approved|complete|completed)$/i,
  },
  {
    tone: 'warning',
    icon: MapPin,
    pattern: /^(outside|late|warning|at risk|review|under review)$/i,
  },
  {
    tone: 'danger',
    icon: XCircle,
    pattern: /^(absent|error|needs improvement|low|rejected|revoked|failed)$/i,
  },
]

const displayLabels = {
  'high performer': 'Excellent',
  average: 'Good',
  'at risk': 'Needs improvement',
  low: 'Needs improvement',
}

export default function StatusBadge({ status, label }) {
  const text = label || displayLabels[String(status || '').toLowerCase()] || status || 'Not classified'
  const rule = statusRules.find(({ pattern }) => pattern.test(String(status || '')))
  const Icon = rule?.icon || Info
  const tone = rule?.tone || 'info'

  return <span className={`status-badge ${tone}`}><Icon size={13} aria-hidden="true" /><span>{text}</span></span>
}
