# Splash, screensaver and popups

Three things layered over your pages. All are optional and live in the config.

## Startup splash

Shown for 1.5 s when the panel connects (name, version, firmware). `splash: {seconds: 3}` or `splash: false`.

## Idle: dim and screensaver

```yaml
idle:
  after: 300            # seconds without a touch
  dim_to: 10            # brightness % while idle (omit to keep brightness)
  off_after: 1800       # brightness 0 after this long (omit to never)
  screensaver: {type: screensaver, text: wigix, style: bounce}   # style: bounce | clock; or `false` to only dim
```

The first touch wakes the screen and is swallowed (it does not press whatever was underneath). The screensaver drifts so
no pixel stays lit. Any widget can be the screensaver (`screensaver: {type: image, source: ~/pics/loop.gif}`).

## Triggers and popups

Show a widget fullscreen for a few seconds when something happens in Home Assistant, and optionally run an action:

```yaml
triggers:
  - name: doorbell
    when: {entity: binary_sensor.doorbell, state: "on"}      # or state_in: [...], above: 30, below: 5
    show: {type: camera, ha_camera: camera.front_door}
    seconds: 20
    wake: true            # leave idle (default true)
    cooldown: 10          # seconds before it can fire again
    action: {ha_service: light.turn_on, entity: light.porch}   # optional, any action
```

Triggers fire on the rising edge (not-matching to matching), skip while Home Assistant is disconnected, and a tap on the
popup dismisses it. You can also open one from any button or tap: `on_tap: {popup: {type: clock}, seconds: 10}`.
