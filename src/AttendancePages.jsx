import { useEffect, useMemo, useRef, useState } from 'react'
import { Bell, Check, MapPin, Plus, Trash2 } from 'lucide-react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  createOfficeLocation,
  deleteOfficeLocation,
  getAttendance,
  getNotifications,
  getOfficeLocations,
  markNotificationRead,
  timeIn,
  timeOut,
  updateOfficeLocation,
} from './services/attendanceService'
import { requestLocation } from './services/attendanceGeolocation'
import { useAuthStore } from './store/useAuthStore'
import StatusBadge from './components/StatusBadge'

function useApiResource(loader, dependencies = []) {
  const loaderRef = useRef(loader)
  const [version, setVersion] = useState(0)
  const [resource, setResource] = useState({ data: null, loading: true, error: '' })
  loaderRef.current = loader
  useEffect(() => {
    let active = true
    setResource((current) => ({ ...current, loading: true, error: '' }))
    loaderRef.current().then((data) => {
      if (active) setResource({ data, loading: false, error: '' })
    }).catch((error) => {
      if (active) setResource({ data: null, loading: false, error: error.response?.data?.detail || error.message || 'Unable to load data.' })
    })
    return () => { active = false }
  }, [version, ...dependencies])
  return { ...resource, retry: () => setVersion((current) => current + 1) }
}

function SectionHeader({ title, action }) {
  return <div className="section-header"><h2>{title}</h2>{action}</div>
}

const outsidePolicies = [
  { value: 'require_reason', label: 'Require a reason' },
  { value: 'block', label: 'Block Time In' },
  { value: 'allow', label: 'Allow and notify' },
]

function manilaToday() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Manila' }).format(new Date())
}

export function AttendancePage() {
  const user = useAuthStore((state) => state.user)
  const resource = useApiResource(getAttendance)
  const [outsideReason, setOutsideReason] = useState('')
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [locationStatus, setLocationStatus] = useState(null)
  const records = resource.data || []
  const ownRecord = useMemo(
    () => records.find((item) => item.user_id === user.id && item.work_date === manilaToday()),
    [records, user.id],
  )
  const canClock = user.role !== 'admin'
  const isOutside = locationStatus?.state === 'outside'
    || (!locationStatus && ownRecord?.is_outside_premises === true)
  const badgeStatus = isOutside
    ? 'Outside'
    : locationStatus?.state === 'checking'
      ? 'Checking'
      : locationStatus?.state === 'inside'
        || (!locationStatus && ownRecord?.is_outside_premises === false)
          ? 'Inside'
          : 'Unknown'
  const statusLabel = locationStatus?.state === 'checking'
    ? 'Checking location…'
    : isOutside
      ? `Outside premises${(locationStatus?.distance ?? ownRecord?.distance_from_office) != null ? `, ${Math.round(locationStatus?.distance ?? ownRecord.distance_from_office)} m away` : ''}`
      : locationStatus?.state === 'inside'
        || (!locationStatus && ownRecord?.is_outside_premises === false)
          ? 'Inside premises'
          : locationStatus?.state === 'unavailable'
            ? 'Location unavailable'
            : 'Location not checked'

  const submitAttendance = async (action) => {
    setBusy(action)
    setLocationStatus({ state: 'checking' })
    setMessage('Getting your location...')
    try {
      const location = await requestLocation()
      if (action === 'time-in') {
        location.outside_reason = outsideReason
        const record = await timeIn(location)
        setLocationStatus({
          state: record.is_outside_premises ? 'outside' : record.is_outside_premises === false ? 'inside' : 'unavailable',
          distance: record.distance_from_office,
        })
        setMessage('Time In recorded.')
      } else {
        const record = await timeOut(location)
        setLocationStatus({
          state: record.is_outside_premises ? 'outside' : record.is_outside_premises === false ? 'inside' : 'unavailable',
          distance: record.distance_from_office,
        })
        setMessage('Time Out recorded.')
      }
      resource.retry()
    } catch (error) {
      if (error.response?.data?.is_outside_premises === true) {
        setLocationStatus({
          state: 'outside',
          distance: error.response.data.distance_from_office,
        })
      } else if (!error.response) {
        setLocationStatus({ state: 'unavailable' })
      } else {
        setLocationStatus(
          ownRecord?.is_outside_premises === true
            ? { state: 'outside', distance: ownRecord.distance_from_office }
            : ownRecord?.is_outside_premises === false
              ? { state: 'inside', distance: ownRecord.distance_from_office }
              : { state: 'unavailable' },
        )
      }
      setMessage(error.response?.data?.detail || error.message || 'Unable to record attendance.')
    } finally {
      setBusy('')
    }
  }

  const canTimeIn = !resource.loading && !ownRecord?.clock_in_at
  const canTimeOut = !resource.loading && Boolean(ownRecord?.clock_in_at && !ownRecord.clock_out_at)
  return <><div className="page-heading"><div><p className="eyebrow">ATTENDANCE</p><h2>Attendance</h2><p className="muted">Location is collected only when you choose Time In or Time Out, and is visible only to you and your managers.</p></div></div>{canClock && <section className="panel settings-form"><SectionHeader title="Your shift" action={<span className="subtle-label">{ownRecord?.work_date || manilaToday()}</span>} /><StatusBadge status={badgeStatus} label={statusLabel} />{ownRecord?.latitude != null && <p className="muted">Recorded coordinates: {Number(ownRecord.latitude).toFixed(6)}, {Number(ownRecord.longitude).toFixed(6)} · GPS accuracy ±{Math.round(ownRecord.accuracy)} m</p>}{isOutside && <label>Reason if outside premises<textarea rows="2" maxLength="1000" value={outsideReason} onChange={(event) => setOutsideReason(event.target.value)} /></label>}<div className="form-actions attendance-action-group"><button type="button" className="primary-button attendance-time-in" disabled={!canTimeIn || Boolean(busy)} onClick={() => submitAttendance('time-in')}>{busy === 'time-in' ? 'Getting location...' : 'Time In'}</button><div className="attendance-time-out"><button type="button" className="secondary-button" disabled={!canTimeOut || Boolean(busy)} onClick={() => submitAttendance('time-out')}>{busy === 'time-out' ? 'Getting location...' : 'Time Out'}</button>{!canTimeOut && <span>Time in first</span>}</div></div>{message && <p role="status">{message}</p>}</section>}<section className="panel list-panel"><SectionHeader title="Location status" action={<MapPin size={18} />} />{resource.loading || resource.error ? <div role={resource.error ? 'alert' : 'status'}>{resource.error || 'Loading attendance...'}</div> : <div className="attendance-location-list">{records.map((record) => <article className="evaluation-row" key={record.id}><div className="evaluation-row-main"><strong>{record.user_name}</strong><span>{record.role}{record.team ? ` · Team: ${record.team}` : ''} · {record.work_date}</span><p>{record.latitude == null ? 'Location not recorded' : `${Number(record.latitude).toFixed(6)}, ${Number(record.longitude).toFixed(6)} · ±${Math.round(record.accuracy)} m`}</p></div><StatusBadge status={record.is_outside_premises === true ? 'Outside' : record.is_outside_premises === false ? 'Inside' : 'Unknown'} /><span>{record.distance_from_office == null ? '—' : `${Math.round(record.distance_from_office)} m`}</span></article>)}</div>}</section></>
}

const emptyOffice = {
  name: '',
  latitude: '',
  longitude: '',
  allowed_radius_meters: 100,
  outside_time_in_policy: 'block',
  is_active: true,
}

export function OfficeLocationsPage() {
  const resource = useApiResource(getOfficeLocations)
  const [form, setForm] = useState(emptyOffice)
  const [editingId, setEditingId] = useState(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const updateForm = (key, value) => setForm((current) => ({ ...current, [key]: value }))
  const submit = async (event) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    const data = {
      ...form,
      latitude: Number(form.latitude),
      longitude: Number(form.longitude),
      allowed_radius_meters: Number(form.allowed_radius_meters),
    }
    try {
      if (editingId) await updateOfficeLocation(editingId, data)
      else await createOfficeLocation(data)
      setForm(emptyOffice)
      setEditingId(null)
      resource.retry()
    } catch (saveError) {
      setError(Object.values(saveError.response?.data || {}).flat().join(' ') || 'Unable to save office location.')
    } finally {
      setSaving(false)
    }
  }
  const edit = (office) => {
    setEditingId(office.id)
    setForm({ ...office })
  }
  const remove = async (id) => {
    setError('')
    try {
      await deleteOfficeLocation(id)
      resource.retry()
    } catch (deleteError) {
      setError(deleteError.response?.data?.detail || 'Unable to remove office location.')
    }
  }
  return <><div className="page-heading"><div><p className="eyebrow">ADMINISTRATION</p><h2>Office locations</h2><p className="muted">Set branch coordinates, the allowed radius and outside-premises time-in policy.</p></div></div><section className="panel settings-form"><SectionHeader title={editingId ? 'Edit office' : 'Add office'} action={<MapPin size={18} />} /><form className="modal-form" onSubmit={submit}><label>Location name<input required maxLength="120" value={form.name} onChange={(event) => updateForm('name', event.target.value)} /></label><div className="form-grid"><label>Latitude<input required type="number" step="any" min="-90" max="90" value={form.latitude} onChange={(event) => updateForm('latitude', event.target.value)} /></label><label>Longitude<input required type="number" step="any" min="-180" max="180" value={form.longitude} onChange={(event) => updateForm('longitude', event.target.value)} /></label><label>Allowed radius (meters)<input required type="number" min="1" value={form.allowed_radius_meters} onChange={(event) => updateForm('allowed_radius_meters', event.target.value)} /></label><label>Outside time-in behavior<select value={form.outside_time_in_policy} onChange={(event) => updateForm('outside_time_in_policy', event.target.value)}>{outsidePolicies.map((policy) => <option value={policy.value} key={policy.value}>{policy.label}</option>)}</select></label></div><label className="preference-row"><span><strong>Active branch</strong><small>Inactive locations are excluded from distance checks.</small></span><input type="checkbox" checked={form.is_active} onChange={(event) => updateForm('is_active', event.target.checked)} /></label>{error && <p className="form-error" role="alert">{error}</p>}<div className="form-actions">{editingId && <button type="button" className="secondary-button" onClick={() => { setEditingId(null); setForm(emptyOffice) }}>Cancel edit</button>}<button type="submit" className="primary-button" disabled={saving}>{saving ? 'Saving...' : editingId ? 'Save changes' : <><Plus size={16} /> Add location</>}</button></div></form></section><section className="panel list-panel"><SectionHeader title="Configured branches" action={<span className="subtle-label">{resource.data?.length || 0}</span>} />{resource.loading || resource.error ? <div role={resource.error ? 'alert' : 'status'}>{resource.error || 'Loading offices...'}</div> : (resource.data || []).map((office) => <article className="evaluation-row" key={office.id}><div className="evaluation-row-main"><strong>{office.name}</strong><span>{Number(office.latitude).toFixed(6)}, {Number(office.longitude).toFixed(6)} · {office.allowed_radius_meters} m radius · {outsidePolicies.find((item) => item.value === office.outside_time_in_policy)?.label}</span></div><button type="button" className="secondary-button" onClick={() => edit(office)}>Edit</button><button type="button" className="icon-button" aria-label={`Remove ${office.name}`} onClick={() => remove(office.id)}><Trash2 size={16} /></button></article>)}</section></>
}

export function NotificationsPage() {
  const resource = useApiResource(getNotifications)
  const [error, setError] = useState('')
  const markRead = async (id) => {
    setError('')
    try {
      await markNotificationRead(id)
      window.dispatchEvent(new Event('lexie:notifications-updated'))
      resource.retry()
    } catch (readError) {
      setError(readError.response?.data?.detail || 'Unable to mark the notification as read.')
    }
  }
  if (resource.loading || resource.error) return <div role={resource.error ? 'alert' : 'status'}>{resource.error || 'Loading notifications...'}</div>
  return <><div className="page-heading"><div><p className="eyebrow">INBOX</p><h2>Notifications</h2><p className="muted">{resource.data.unread_count} unread</p></div><Bell size={20} /></div>{error && <p className="form-error" role="alert">{error}</p>}<section className="panel list-panel">{resource.data.results.length ? resource.data.results.map((notification) => <article className="evaluation-row" key={notification.id}><div className="evaluation-row-main"><strong>{notification.title}</strong><span>{new Date(notification.created_at).toLocaleString()}</span><p>{notification.message}</p></div>{!notification.read_at && <button className="secondary-button" onClick={() => markRead(notification.id)}><Check size={16} /> Mark read</button>}</article>) : <div className="empty-state">No notifications yet.</div>}</section></>
}

export function NotificationBell() {
  const user = useAuthStore((state) => state.user)
  const location = useLocation()
  const [refreshVersion, setRefreshVersion] = useState(0)
  useEffect(() => {
    const refresh = () => setRefreshVersion((current) => current + 1)
    window.addEventListener('lexie:notifications-updated', refresh)
    const timer = window.setInterval(refresh, 60000)
    return () => {
      window.removeEventListener('lexie:notifications-updated', refresh)
      window.clearInterval(timer)
    }
  }, [])
  const resource = useApiResource(getNotifications, [location.pathname, refreshVersion])
  if (!user || user.role === 'client' || resource.error) return null
  return <NavLink to={`/${user.role}/notifications`} className="notification-link" aria-label={`Notifications${resource.data?.unread_count ? `, ${resource.data.unread_count} unread` : ''}`} title="Notifications"><Bell size={18} />{resource.data?.unread_count > 0 && <span className="notification-count">{resource.data.unread_count}</span>}</NavLink>
}
