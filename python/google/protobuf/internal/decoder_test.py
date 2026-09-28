# -*- coding: utf-8 -*-
# Protocol Buffers - Google's data interchange format
# Copyright 2008 Google Inc.  All rights reserved.
#
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file or at
# https://developers.google.com/open-source/licenses/bsd

"""Test decoder."""

import unittest

from google.protobuf import message
from google.protobuf.internal import api_implementation
from google.protobuf.internal import decoder
from google.protobuf.internal import encoder
from google.protobuf.internal import testing_refleaks
from google.protobuf.internal import wire_format

from google.protobuf import struct_pb2
from google.protobuf import unittest_mset_pb2
from google.protobuf import unittest_mset_wire_format_pb2
from google.protobuf import unittest_pb2


@testing_refleaks.TestCase
class DecoderTest(unittest.TestCase):

  def test_decode_unknown_group_field_too_many_levels(self):
    data = memoryview(b'\023' * 5_000_000)
    self.assertRaisesRegex(
        message.DecodeError,
        'Error parsing message',
        decoder._DecodeUnknownField,
        data,
        1,
        wire_format.WIRETYPE_START_GROUP,
    )

  def _MakeRecursiveGroupMessage(self, n):
    """Serializes a message nested 4 * n + 1 levels deep through a group."""
    msg = unittest_pb2.TestMutualRecursionA()
    sub = msg
    for _ in range(n):
      sub = sub.subgroup.sub_message.b.a
    sub.bb.optional_int32 = 1
    return msg.SerializeToString()

  def test_decode_group_field_ok_sized(self):
    data = self._MakeRecursiveGroupMessage(24)  # 97 levels of nesting.
    msg = unittest_pb2.TestMutualRecursionA()
    msg.ParseFromString(data)
    self.assertTrue(msg.HasField('subgroup'))

  def test_decode_group_field_too_many_levels(self):
    data = self._MakeRecursiveGroupMessage(30)  # 121 levels of nesting.
    msg = unittest_pb2.TestMutualRecursionA()
    with self.assertRaises(message.DecodeError) as context:
      msg.ParseFromString(data)
    self.assertIn('Error parsing message', str(context.exception))
    if api_implementation.Type() == 'python':
      self.assertIn('too many levels of nesting', str(context.exception))

  def test_decode_group_field_respects_recursion_limit(self):
    if api_implementation.Type() != 'python':
      self.skipTest('SetRecursionLimit only applies to the python decoder')
    data = self._MakeRecursiveGroupMessage(30)  # 121 levels of nesting.
    msg = unittest_pb2.TestMutualRecursionA()
    decoder.SetRecursionLimit(200)
    try:
      msg.ParseFromString(data)
    finally:
      decoder.SetRecursionLimit(decoder.DEFAULT_RECURSION_LIMIT)
    self.assertTrue(msg.HasField('subgroup'))

  def _MakeRecursiveMapMessage(self, n):
    """Builds Struct wire bytes nested n levels through its map<string, Value>.

    Struct.fields is a map<string, Value> and Value.struct_value is a Struct, so
    each level descends through the map decoder (DecodeMap). The bytes are built
    directly rather than by serializing a live message, because materializing a
    deeply nested message in Python would itself recurse while being built.
    """
    def _varint(v):
      out = b''
      while True:
        b = v & 0x7F
        v >>= 7
        out += bytes([b | 0x80]) if v else bytes([b])
        if not v:
          return out
    def _ld(field, payload):
      return _varint((field << 3) | 2) + _varint(len(payload)) + payload
    data = b''
    for _ in range(n):
      value = _ld(5, data)                   # Value.struct_value = inner Struct
      entry = _ld(1, b'x') + _ld(2, value)   # MapEntry key="x", value=Value
      data = _ld(1, entry)                   # Struct.fields <- entry
    return data

  def test_decode_map_value_ok_sized(self):
    data = self._MakeRecursiveMapMessage(10)
    msg = struct_pb2.Struct()
    msg.ParseFromString(data)
    self.assertIn('x', msg.fields)

  def test_decode_map_value_too_many_levels(self):
    data = self._MakeRecursiveMapMessage(200)
    msg = struct_pb2.Struct()
    with self.assertRaises(message.DecodeError) as context:
      msg.ParseFromString(data)
    if api_implementation.Type() == 'python':
      self.assertIn('too many levels of nesting', str(context.exception))

  def _MakeMessageSetItem(self, type_id, payload, prefix=b''):
    """Builds wire bytes of one MessageSet item group.

    Args:
      type_id: extension number stored in the item.
      payload: serialized extension message.
      prefix: raw bytes placed inside the item before type_id.

    Returns:
      bytes of the item, including its start and end group tags.
    """
    return (
        encoder.TagBytes(1, wire_format.WIRETYPE_START_GROUP)
        + prefix
        + encoder.TagBytes(2, wire_format.WIRETYPE_VARINT)
        + encoder._VarintBytes(type_id)
        + encoder.TagBytes(3, wire_format.WIRETYPE_LENGTH_DELIMITED)
        + encoder._VarintBytes(len(payload))
        + payload
        + encoder.TagBytes(1, wire_format.WIRETYPE_END_GROUP)
    )

  def _MakeRecursiveMessageSet(self, n):
    """Builds TestMessageSet wire bytes nested 2 * n levels deep.

    Each level goes through a MessageSet item (DecodeItem) holding a
    TestMessageSetExtension1 whose `recursive` field is another TestMessageSet.
    The bytes are built directly rather than by serializing a live message.
    """
    type_id = (
        unittest_mset_pb2.TestMessageSetExtension1.message_set_extension.number
    )
    data = b''
    for _ in range(n):
      ext = (
          encoder.TagBytes(16, wire_format.WIRETYPE_LENGTH_DELIMITED)
          + encoder._VarintBytes(len(data))
          + data
      )
      data = self._MakeMessageSetItem(type_id, ext)
    return data

  def test_decode_message_set_ok_sized(self):
    data = self._MakeRecursiveMessageSet(10)  # 20 levels of nesting.
    msg = unittest_mset_wire_format_pb2.TestMessageSet()
    msg.ParseFromString(data)
    ext = unittest_mset_pb2.TestMessageSetExtension1.message_set_extension
    self.assertTrue(msg.Extensions[ext].HasField('recursive'))

  def test_decode_message_set_too_many_levels(self):
    data = self._MakeRecursiveMessageSet(500)  # 1000 levels of nesting.
    msg = unittest_mset_wire_format_pb2.TestMessageSet()
    with self.assertRaises(message.DecodeError) as context:
      msg.ParseFromString(data)
    if api_implementation.Type() == 'python':
      self.assertIn('too many levels of nesting', str(context.exception))

  def test_decode_message_set_unknown_group_ok_sized(self):
    type_id = (
        unittest_mset_pb2.TestMessageSetExtension1.message_set_extension.number
    )
    payload = unittest_mset_pb2.TestMessageSetExtension1(i=7).SerializeToString()
    start = encoder.TagBytes(4, wire_format.WIRETYPE_START_GROUP)
    end = encoder.TagBytes(4, wire_format.WIRETYPE_END_GROUP)
    unknown = start * 3 + b'\x08\x01' + end * 3
    data = self._MakeMessageSetItem(type_id, payload, prefix=unknown)
    msg = unittest_mset_wire_format_pb2.TestMessageSet()
    msg.ParseFromString(data)
    ext = unittest_mset_pb2.TestMessageSetExtension1.message_set_extension
    self.assertEqual(7, msg.Extensions[ext].i)

  def test_decode_message_set_unknown_group_too_many_levels(self):
    # A MessageSet item carrying an unknown field that opens an unbounded
    # series of groups.
    data = encoder.TagBytes(1, wire_format.WIRETYPE_START_GROUP) + (
        encoder.TagBytes(4, wire_format.WIRETYPE_START_GROUP) * 5_000_000
    )
    msg = unittest_mset_wire_format_pb2.TestMessageSet()
    with self.assertRaises(message.DecodeError) as context:
      msg.ParseFromString(data)
    if api_implementation.Type() == 'python':
      self.assertIn('too many levels of nesting', str(context.exception))


if __name__ == '__main__':
  unittest.main()
