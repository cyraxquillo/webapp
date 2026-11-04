export const GPS_ACCURACY_LIMIT_METERS = 100

export function getLocationError(error) {
  if (error.code === 1) return 'Location permission was denied. Allow location access in your browser and try again.'
  if (error.code === 2) return 'Your device could not determine a location. Check that GPS/location services are on.'
  if (error.code === 3) return 'Location request timed out. Move to an open area and try again.'
  return 'Unable to get your location. Check browser permissions and try again.'
}

export function requestLocation(geolocation = globalThis.navigator?.geolocation) {
  return new Promise((resolve, reject) => {
    if (!geolocation) {
      reject(new Error('This browser does not support location access. Use a supported browser to record attendance.'))
      return
    }
    geolocation.getCurrentPosition((position) => {
      if (position.coords.accuracy > GPS_ACCURACY_LIMIT_METERS) {
        reject(new Error(`GPS accuracy is too low (±${Math.round(position.coords.accuracy)} m). Move to an open area and try again.`))
        return
      }
      resolve({
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy: position.coords.accuracy,
      })
    }, (error) => reject(new Error(getLocationError(error))), {
      enableHighAccuracy: true,
      timeout: 15000,
      maximumAge: 0,
    })
  })
}
