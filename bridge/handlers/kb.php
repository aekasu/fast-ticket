<?php

require_once INCLUDE_DIR . 'class.faq.php';
require_once INCLUDE_DIR . 'class.canned.php';
require_once dirname(__FILE__) . '/common.php';

class FastApiKbHandler {
    
    // ── Categories ─────────────────────────────────────────────────────────
    //
    // Category::create() only instantiates; Category::update($vars, &$errors) validates and
    // saves and OVERWRITES every field, so updates start from the stored state. osTicket
    // requires a name of 3+ characters and a non-empty description. `is_public` maps to
    // Category::VISIBILITY_PUBLIC / _PRIVATE; a featured category stays featured.

    private static function requireCategory($category_id) {
        $category = Category::lookup((int) $category_id);
        if (!$category) {
            throw new Exception("Category not found: {$category_id}");
        }
        return $category;
    }

    private static function categoryVisibility($is_public, $stored) {
        if ($is_public === null) {
            return (int) $stored;
        }
        if (!$is_public) {
            return Category::VISIBILITY_PRIVATE;
        }
        return ((int) $stored === Category::VISIBILITY_FEATURED) ? Category::VISIBILITY_FEATURED : Category::VISIBILITY_PUBLIC;
    }

    public static function createCategory($payload, $staff_id) {
        $vars = array(
            'name' => isset($payload['name']) ? trim($payload['name']) : '',
            'description' => isset($payload['description']) ? $payload['description'] : '',
            'ispublic' => self::categoryVisibility(isset($payload['is_public']) ? (bool) $payload['is_public'] : true, 0),
            'pid' => 0,
            'notes' => isset($payload['notes']) ? $payload['notes'] : '',
        );
        $category = Category::create();
        FastApiModel::saveViaUpdate($category, $vars, 'Category');
        return array('category_id' => $category->getId(), 'name' => $category->name);
    }

    public static function updateCategory($payload, $staff_id) {
        $category = self::requireCategory($payload['category_id'] ?? 0);
        $vars = array(
            'id' => $category->getId(),
            'name' => isset($payload['name']) ? trim($payload['name']) : $category->name,
            'description' => isset($payload['description']) ? $payload['description'] : $category->description,
            'ispublic' => self::categoryVisibility(isset($payload['is_public']) ? (bool) $payload['is_public'] : null, $category->ispublic),
            'pid' => $category->category_pid,
            'notes' => isset($payload['notes']) ? $payload['notes'] : $category->notes,
        );
        FastApiModel::saveViaUpdate($category, $vars, 'Category');
        return array('category_id' => $category->getId());
    }

    public static function deleteCategory($payload, $staff_id) {
        $category = self::requireCategory($payload['category_id'] ?? 0);
        if (!$category->delete()) {
            throw new Exception('Category could not be deleted (it may still contain FAQs).');
        }
        return array('category_id' => (int) ($payload['category_id'] ?? 0));
    }

    // ── FAQs ───────────────────────────────────────────────────────────────
    //
    // FAQ::create() only instantiates; FAQ::update($vars, &$errors) validates and saves
    // and OVERWRITES every field (and unlinks help topics when `topics` is absent), so
    // updates start from the stored state. `is_published` maps to FAQ::VISIBILITY_PUBLIC /
    // _PRIVATE; a featured FAQ stays featured. FAQ::update() forces the deprecated
    // `keywords` column to ' ', so keywords are written afterwards to honour the API.

    private static function requireFaq($faq_id) {
        $faq = FAQ::lookup((int) $faq_id);
        if (!$faq) {
            throw new Exception("FAQ not found: {$faq_id}");
        }
        return $faq;
    }

    private static function faqVisibility($is_published, $stored) {
        if ($is_published === null) {
            return (int) $stored;
        }
        if (!$is_published) {
            return FAQ::VISIBILITY_PRIVATE;
        }
        return ((int) $stored === FAQ::VISIBILITY_FEATURED) ? FAQ::VISIBILITY_FEATURED : FAQ::VISIBILITY_PUBLIC;
    }

    private static function saveFaq(FAQ $faq, array $vars, $keywords) {
        FastApiModel::saveViaUpdate($faq, $vars, 'FAQ');
        if ($keywords !== null && $faq->keywords !== $keywords) {
            $faq->keywords = $keywords;
            $faq->save();
        }
    }

    public static function createFaq($payload, $staff_id) {
        $vars = array(
            'category_id' => isset($payload['category_id']) ? (int) $payload['category_id'] : 0,
            'question' => isset($payload['question']) ? trim($payload['question']) : '',
            'answer' => isset($payload['answer']) ? $payload['answer'] : '',
            'ispublished' => self::faqVisibility(isset($payload['is_published']) ? (bool) $payload['is_published'] : false, 0),
            'notes' => isset($payload['notes']) ? $payload['notes'] : '',
            'topics' => array(),
        );
        $faq = FAQ::create();
        $uploads = FastApiAttachments::upload((array) ($payload['attachments'] ?? []));
        if ($uploads) {
            $vars['files'] = array_column($uploads, 'name', 'id');   // FAQ::update() keeps exactly these files
        }
        self::saveFaq($faq, $vars, isset($payload['keywords']) ? $payload['keywords'] : null);
        return array('faq_id' => $faq->getId(), 'question' => $faq->question);
    }

    public static function updateFaq($payload, $staff_id) {
        $faq = self::requireFaq($payload['faq_id'] ?? 0);
        $stored_keywords = $faq->keywords;
        $topics = array();
        foreach ($faq->topics as $link) {
            $topics[] = $link->topic_id;   // update() would unlink all of them if omitted
        }
        $vars = array(
            'id' => $faq->getId(),
            'category_id' => isset($payload['category_id']) ? (int) $payload['category_id'] : $faq->category_id,
            'question' => isset($payload['question']) ? trim($payload['question']) : $faq->question,
            'answer' => isset($payload['answer']) ? $payload['answer'] : $faq->answer,
            'ispublished' => self::faqVisibility(isset($payload['is_published']) ? (bool) $payload['is_published'] : null, $faq->ispublished),
            'notes' => isset($payload['notes']) ? $payload['notes'] : $faq->notes,
            'topics' => $topics,
        );
        $keep = FastApiAttachments::keepSet($faq->getAttachments(), $payload);
        if ($keep !== null) {
            $vars['files'] = $keep;
        }
        // FAQ::update() blanks the deprecated keywords column, so always restore the wanted value.
        $keywords = isset($payload['keywords']) ? $payload['keywords'] : $stored_keywords;
        self::saveFaq($faq, $vars, $keywords);
        return array('faq_id' => $faq->getId());
    }

    public static function deleteFaq($payload, $staff_id) {
        $faq = self::requireFaq($payload['faq_id'] ?? 0);
        if (!$faq->delete()) {
            throw new Exception('FAQ could not be deleted.');
        }
        return array('faq_id' => (int) ($payload['faq_id'] ?? 0));
    }

    // ── Canned Responses ───────────────────────────────────────────────────

    public static function createCanned($payload, $staff_id) {
        if (!isset($payload['title']) || !isset($payload['response'])) {
            throw new Exception("title and response are required.");
        }

        $vars = array(
            'title' => $payload['title'],
            'response' => $payload['response'],
            'dept_id' => isset($payload['dept_id']) ? (int)$payload['dept_id'] : 0,
            'isenabled' => isset($payload['is_enabled']) ? (int)$payload['is_enabled'] : 1,
            'notes' => isset($payload['notes']) ? $payload['notes'] : ''
        );

        // Canned::create() only instantiates an unsaved model; update() validates and
        // saves it. update() can return true while filling $errors, so check both.
        $errors = array();
        $canned = Canned::create();
        if (!$canned->update($vars, $errors) || !empty($errors) || !$canned->getId()) {
            $error_msg = !empty($errors) ? json_encode($errors) : 'Unknown error creating Canned Response.';
            throw new Exception("Failed to create Canned Response: " . $error_msg);
        }

        // osTicket's ORM only marks a field dirty when its value changes, and an unset
        // field assigned 0 compares equal to null — so a new row created with
        // isenabled=0 is inserted with the column default (1). Re-apply it now that the
        // row exists so "disabled" sticks.
        if ((int)$canned->isenabled !== $vars['isenabled']) {
            $canned->isenabled = $vars['isenabled'];
            $canned->save();
        }

        // scp/canned.php attaches the uploaded files after the record exists
        $uploads = FastApiAttachments::upload((array) ($payload['attachments'] ?? []));
        if ($uploads) {
            $canned->attachments->keepOnlyFileIds(array_column($uploads, 'name', 'id'), false);
        }

        return array('canned_id' => $canned->getId());
    }

    public static function updateCanned($payload, $staff_id) {
        if (!isset($payload['canned_id'])) {
            throw new Exception("Canned Response ID ('canned_id') is required.");
        }

        $canned = Canned::lookup($payload['canned_id']);
        if (!$canned) {
            throw new Exception("Canned Response not found.");
        }

        // Canned::update() overwrites every field, but the API sends partial updates:
        // start from the stored values and overlay whatever the payload provides.
        $vars = array(
            'id' => $canned->getId(),
            'title' => isset($payload['title']) ? $payload['title'] : $canned->title,
            'response' => isset($payload['response']) ? $payload['response'] : $canned->response,
            'dept_id' => isset($payload['dept_id']) ? (int)$payload['dept_id'] : $canned->dept_id,
            'isenabled' => isset($payload['is_enabled']) ? (int)$payload['is_enabled'] : $canned->isenabled,
            'notes' => isset($payload['notes']) ? $payload['notes'] : $canned->notes
        );
        $errors = array();

        $keep = FastApiAttachments::keepSet($canned->attachments, $payload);
        if (!$canned->update($vars, $errors) || !empty($errors)) {
            $error_msg = !empty($errors) ? json_encode($errors) : 'Unknown error updating Canned Response.';
            throw new Exception("Failed to update Canned Response: " . $error_msg);
        }
        if ($keep !== null) {
            $canned->attachments->keepOnlyFileIds($keep, false);
        }

        return array('canned_id' => $canned->getId());
    }

    public static function deleteCanned($payload, $staff_id) {
        if (!isset($payload['canned_id'])) {
            throw new Exception("Canned Response ID ('canned_id') is required.");
        }

        $canned = Canned::lookup($payload['canned_id']);
        if (!$canned) {
            throw new Exception("Canned Response not found.");
        }

        if (!$canned->delete()) {
            throw new Exception("Failed to delete Canned Response.");
        }

        return array('success' => true);
    }
}
