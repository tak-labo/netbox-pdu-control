from netbox.plugins import PluginTemplateExtension


class DeviceManagedPDUButton(PluginTemplateExtension):
    models = ["dcim.device"]

    def buttons(self):
        device = self.context["object"]
        try:
            pdu = device.managed_pdu
        except Exception:
            return ""
        return self.render(
            "netbox_pdu_control/inc/device_pdu_button.html",
            extra_context={"pdu": pdu},
        )

    def right_page(self):
        device = self.context["object"]
        output = []

        outlets = list(device.pdu_outlets.select_related("managed_pdu").order_by("managed_pdu", "outlet_number"))
        if outlets:

            def _sum(field):
                vals = [getattr(o, field) for o in outlets if getattr(o, field) is not None]
                return sum(vals) if vals else None

            updated_times = [o.last_updated_from_pdu for o in outlets if o.last_updated_from_pdu]
            output.append(
                self.render(
                    "netbox_pdu_control/inc/device_pdu_outlets.html",
                    extra_context={
                        "pdu_outlets": outlets,
                        "total_power_w": _sum("power_w"),
                        "total_current_a": _sum("current_a"),
                        "total_energy_wh": _sum("energy_wh"),
                        "last_updated": max(updated_times) if updated_times else None,
                    },
                )
            )

        try:
            managed_pdu = device.managed_pdu
        except Exception:
            managed_pdu = None

        if managed_pdu:
            inlets = list(managed_pdu.inlets.order_by("inlet_number"))
            if inlets:

                def _sum_inlet(field):
                    vals = [getattr(i, field) for i in inlets if getattr(i, field) is not None]
                    return sum(vals) if vals else None

                updated_times = [i.last_updated_from_pdu for i in inlets if i.last_updated_from_pdu]
                output.append(
                    self.render(
                        "netbox_pdu_control/inc/device_pdu_inlets.html",
                        extra_context={
                            "pdu_inlets": inlets,
                            "total_power_w": _sum_inlet("power_w"),
                            "total_current_a": _sum_inlet("current_a"),
                            "total_energy_wh": _sum_inlet("energy_wh"),
                            "last_updated": max(updated_times) if updated_times else None,
                        },
                    )
                )

        return "".join(output)


template_extensions = [DeviceManagedPDUButton]
