import assert from 'node:assert/strict'
import test from 'node:test'
import { requestLocation } from './attendanceGeolocation.js'

test('permission denial explains how to recover', async () => {
  const geolocation = {
    getCurrentPosition(success, failure) {
      failure({ code: 1 })
    },
  }

  await assert.rejects(
    requestLocation(geolocation),
    /Location permission was denied\. Allow location access/,
  )
})

test('GPS unavailable, timeout and low accuracy return actionable errors', async () => {
  for (const [code, message] of [
    [2, /GPS\/location services are on/],
    [3, /timed out/],
  ]) {
    const geolocation = {
      getCurrentPosition(success, failure) {
        failure({ code })
      },
    }
    await assert.rejects(requestLocation(geolocation), message)
  }

  const lowAccuracy = {
    getCurrentPosition(success) {
      success({ coords: { latitude: 0, longitude: 0, accuracy: 101 } })
    },
  }
  await assert.rejects(requestLocation(lowAccuracy), /GPS accuracy is too low/)
})
