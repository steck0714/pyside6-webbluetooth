// Type declarations for the navigator.bluetooth surface implemented by
// pyside6-webbluetooth (v0.0.0).
//
// This mirrors the subset of the Web Bluetooth API
// (https://webbluetoothcg.github.io/web-bluetooth/) that this package
// implements. See CHANGELOG.md / README.md for what is intentionally left
// unimplemented in this version (getIncludedService(s),
// watchAdvertisements(), the manufacturer-data blocklist).

interface BluetoothLEScanFilterInit {
  services?: (string | number)[];
  name?: string;
  namePrefix?: string;
  manufacturerData?: BluetoothManufacturerDataFilterInit[];
  serviceData?: BluetoothServiceDataFilterInit[];
}

interface BluetoothManufacturerDataFilterInit {
  companyIdentifier: number;
  dataPrefix?: BufferSource;
  mask?: BufferSource;
}

interface BluetoothServiceDataFilterInit {
  service: string | number;
  dataPrefix?: BufferSource;
  mask?: BufferSource;
}

interface RequestDeviceOptions {
  filters?: BluetoothLEScanFilterInit[];
  optionalServices?: (string | number)[];
  optionalManufacturerData?: number[];
  acceptAllDevices?: boolean;
}

interface BluetoothCharacteristicProperties {
  readonly broadcast: boolean;
  readonly read: boolean;
  readonly writeWithoutResponse: boolean;
  readonly write: boolean;
  readonly notify: boolean;
  readonly indicate: boolean;
  readonly authenticatedSignedWrites: boolean;
  readonly reliableWrite: boolean;
  readonly writableAuxiliaries: boolean;
}

declare class BluetoothRemoteGATTDescriptor {
  readonly characteristic: BluetoothRemoteGATTCharacteristic;
  readonly uuid: string;
  readonly value: DataView | null;
  readValue(): Promise<DataView>;
  writeValue(value: BufferSource): Promise<void>;
}

declare class BluetoothRemoteGATTCharacteristic extends EventTarget {
  readonly service: BluetoothRemoteGATTService;
  readonly uuid: string;
  readonly properties: BluetoothCharacteristicProperties;
  readonly value: DataView | null;
  oncharacteristicvaluechanged: ((this: BluetoothRemoteGATTCharacteristic, ev: Event) => any) | null;

  getDescriptor(descriptor: string | number): Promise<BluetoothRemoteGATTDescriptor>;
  getDescriptors(descriptor?: string | number): Promise<BluetoothRemoteGATTDescriptor[]>;
  readValue(): Promise<DataView>;
  writeValueWithResponse(value: BufferSource): Promise<void>;
  writeValueWithoutResponse(value: BufferSource): Promise<void>;
  /** @deprecated Prefer writeValueWithResponse()/writeValueWithoutResponse(). */
  writeValue(value: BufferSource): Promise<void>;
  startNotifications(): Promise<BluetoothRemoteGATTCharacteristic>;
  stopNotifications(): Promise<BluetoothRemoteGATTCharacteristic>;
}

declare class BluetoothRemoteGATTService extends EventTarget {
  readonly device: BluetoothDevice;
  readonly uuid: string;
  readonly isPrimary: boolean;

  getCharacteristic(characteristic: string | number): Promise<BluetoothRemoteGATTCharacteristic>;
  getCharacteristics(characteristic?: string | number): Promise<BluetoothRemoteGATTCharacteristic[]>;
  /** Not implemented in v0.0.0; always rejects with NotSupportedError. */
  getIncludedService(service: string | number): Promise<BluetoothRemoteGATTService>;
  /** Not implemented in v0.0.0; always rejects with NotSupportedError. */
  getIncludedServices(service?: string | number): Promise<BluetoothRemoteGATTService[]>;
}

declare class BluetoothRemoteGATTServer {
  readonly device: BluetoothDevice;
  readonly connected: boolean;
  connect(): Promise<BluetoothRemoteGATTServer>;
  disconnect(): void;
  getPrimaryService(service: string | number): Promise<BluetoothRemoteGATTService>;
  getPrimaryServices(service?: string | number): Promise<BluetoothRemoteGATTService[]>;
}

declare class BluetoothDevice extends EventTarget {
  readonly id: string;
  readonly name: string | null;
  readonly gatt: BluetoothRemoteGATTServer;
  readonly watchingAdvertisements: boolean;

  ongattserverdisconnected: ((this: BluetoothDevice, ev: Event) => any) | null;
  onadvertisementreceived: ((this: BluetoothDevice, ev: Event) => any) | null;

  /** Not implemented in v0.0.0; always rejects with NotSupportedError. */
  watchAdvertisements(): Promise<void>;
  unwatchAdvertisements(): void;
  forget(): Promise<void>;
}

declare class Bluetooth extends EventTarget {
  onavailabilitychanged: ((this: Bluetooth, ev: Event) => any) | null;
  getAvailability(): Promise<boolean>;
  getDevices(): Promise<BluetoothDevice[]>;
  requestDevice(options?: RequestDeviceOptions): Promise<BluetoothDevice>;
}

interface Navigator {
  readonly bluetooth: Bluetooth;
}
