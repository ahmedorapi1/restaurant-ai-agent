import unittest
from unittest.mock import Mock

from app.delivery import DeliveryFields, check_delivery_fields, create_delivery_extractor


class DeliveryDetailsTests(unittest.TestCase):
    def test_passes_original_message_and_uses_only_explicit_fields(self):
        text = "التوصيل في مدينتي B6\nمجموعة ١٢ عمارة ٣ شقة ٤"
        address = "مدينتي B6\nمجموعة ١٢ عمارة ٣ شقة ٤"
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = {
            "parsed": DeliveryFields(full_name="اسم متخيّل", phone_number=None, address=address),
        }

        updates = create_delivery_extractor(model)(text, ["address"])

        self.assertEqual(updates, {"address": address})
        sent = model.with_structured_output.return_value.invoke.call_args.args[0]
        self.assertEqual(sent[-1].content, text)

    def test_validates_required_values_and_normalizes_only_the_phone(self):
        details = {
            "full_name": "سلمى حسن",
            "phone_number": "+٢٠ (١٠) ١٢٣٤-٥٦٧٨",
            "address": "مدينتي B6\nمجموعة ١٢ عمارة ٣ شقة ٤",
        }
        valid, missing = check_delivery_fields(details)
        self.assertEqual(missing, [])
        self.assertEqual(valid, {**details, "phone_number": "01012345678"})
        _, missing = check_delivery_fields({"full_name": "سلمى", "phone_number": "123", "address": "الدقي"})
        self.assertEqual(missing, ["full_name", "phone_number", "address"])
