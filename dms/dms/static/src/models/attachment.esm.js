/** ********************************************************************************
    Copyright 2024 Subteno - Timothée Vannier (https://www.subteno.com).
    License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
 **********************************************************************************/
import {Attachment} from "@mail/core/common/attachment_model";
import {patch} from "@web/core/utils/patch";

/**
 * A dms.file is not an ir.attachment, so it cannot be streamed through the
 * /web/content/<id> route: it needs an explicit model/field pair.
 *
 * Since 19.0 every derived URL -- defaultSource, downloadUrl and the PDF.js
 * viewer URL -- is built by FileModelMixin out of `urlRoute` and
 * `urlQueryParams`, so overriding those two is enough. The previous
 * reimplementation of defaultSource/downloadUrl/_handleImage/_handlePdf is gone
 * along with the properties it relied on (`originThread`, `accessToken` and the
 * `mail.channel` model, all removed upstream).
 *
 * `model_name` is set by the DMS views when they insert the attachment record,
 * see file_kanban_record.esm.js and preview_record.esm.js.
 */
patch(Attachment.prototype, {
    get isDmsFile() {
        return this.model_name === "dms.file";
    },

    get urlRoute() {
        if (this.isDmsFile) {
            return "/web/content";
        }
        return super.urlRoute;
    },

    get urlQueryParams() {
        if (this.isDmsFile) {
            return {
                id: this.id,
                model: "dms.file",
                field: "content",
                filename_field: "name",
            };
        }
        return super.urlQueryParams;
    },
});
