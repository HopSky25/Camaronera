/* Copyright 2025 Carlos Roca - Tecnativa
 * License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl). */
import {MediaDialog, TABS} from "@html_editor/main/media/media_dialog/media_dialog";
import {DMSSelector} from "./dms_selector.esm";
import {patch} from "@web/core/utils/patch";

patch(TABS, {
    DMS: {
        id: "DMS",
        title: "DMS",
        Component: DMSSelector,
        // 19.0 orders the notebook pages by sequence; images are 10, icons 20
        // and anything unset falls back to 50. Sit right after the built-ins.
        sequence: 30,
    },
});

patch(MediaDialog.prototype, {
    get initialActiveTab() {
        const dmsTab = this.tabs.DMS;
        if (
            !this.props.activeTab &&
            dmsTab &&
            this.props.media &&
            this.props.media.classList.contains("o_dms_file")
        ) {
            return dmsTab.id;
        }
        return super.initialActiveTab;
    },

    /**
     * 19.0 renamed addDefaultTabs() to addTabs().
     *
     * There is now a plugin resource for this, "media_dialog_extra_tabs", but
     * it only reaches dialogs opened through media_plugin: the x2many html
     * field builds its own dialog with a hardcoded extraTabs list and would
     * silently drop the DMS tab. Patching keeps the 18.0 coverage.
     */
    addTabs() {
        const res = super.addTabs(...arguments);
        const onlyImages =
            this.props.onlyImages ||
            this.props.multiImages ||
            (this.props.media &&
                this.props.media.parentElement &&
                (this.props.media.parentElement.dataset.oeField === "image" ||
                    this.props.media.parentElement.dataset.oeType === "image"));
        const noDMS = onlyImages || this.props.noDMS;
        if (!noDMS) {
            this.addTab(TABS.DMS);
        }
        return res;
    },
});
