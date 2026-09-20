<?php

require_once INCLUDE_DIR . 'class.faq.php';
require_once INCLUDE_DIR . 'class.canned.php';

class FastApiKbHandler {
    
    // ── Categories ─────────────────────────────────────────────────────────

    public static function createCategory($payload, $staff_id) {
        if (!isset($payload['name']) || trim($payload['name']) === '') {
            throw new Exception("Category 'name' is required.");
        }

        $vars = array(
            'name' => trim($payload['name']),
            'description' => isset($payload['description']) ? $payload['description'] : '',
            'ispublic' => isset($payload['ispublic']) ? (int)$payload['ispublic'] : 0,
            'notes' => isset($payload['notes']) ? $payload['notes'] : ''
        );

        $errors = array();
        $category = Category::create($vars, $errors);
        
        if (!$category || !empty($errors)) {
            $error_msg = !empty($errors) ? json_encode($errors) : 'Unknown error creating category.';
            throw new Exception("Failed to create category: " . $error_msg);
        }

        return array('category_id' => $category->getId());
    }

    public static function updateCategory($payload, $staff_id) {
        if (!isset($payload['category_id'])) {
            throw new Exception("Category ID ('category_id') is required.");
        }

        $category = Category::lookup($payload['category_id']);
        if (!$category) {
            throw new Exception("Category not found.");
        }

        $vars = $payload;
        $errors = array();
        
        if (!$category->update($vars, $errors)) {
            $error_msg = !empty($errors) ? json_encode($errors) : 'Unknown error updating category.';
            throw new Exception("Failed to update category: " . $error_msg);
        }

        return array('category_id' => $category->getId());
    }

    public static function deleteCategory($payload, $staff_id) {
        if (!isset($payload['category_id'])) {
            throw new Exception("Category ID ('category_id') is required.");
        }

        $category = Category::lookup($payload['category_id']);
        if (!$category) {
            throw new Exception("Category not found.");
        }

        if (!$category->delete()) {
            throw new Exception("Failed to delete category.");
        }

        return array('success' => true);
    }

    // ── FAQs ───────────────────────────────────────────────────────────────

    public static function createFaq($payload, $staff_id) {
        if (!isset($payload['category_id']) || !isset($payload['question']) || !isset($payload['answer'])) {
            throw new Exception("category_id, question, and answer are required.");
        }

        $vars = array(
            'category_id' => $payload['category_id'],
            'question' => $payload['question'],
            'answer' => $payload['answer'],
            'ispublished' => isset($payload['ispublished']) ? (int)$payload['ispublished'] : 0,
            'notes' => isset($payload['notes']) ? $payload['notes'] : ''
        );

        $errors = array();
        $faq = FAQ::create($vars, $errors);
        
        if (!$faq || !empty($errors)) {
            $error_msg = !empty($errors) ? json_encode($errors) : 'Unknown error creating FAQ.';
            throw new Exception("Failed to create FAQ: " . $error_msg);
        }

        return array('faq_id' => $faq->getId());
    }

    public static function updateFaq($payload, $staff_id) {
        if (!isset($payload['faq_id'])) {
            throw new Exception("FAQ ID ('faq_id') is required.");
        }

        $faq = FAQ::lookup($payload['faq_id']);
        if (!$faq) {
            throw new Exception("FAQ not found.");
        }

        $vars = $payload;
        $errors = array();
        
        if (!$faq->update($vars, $errors)) {
            $error_msg = !empty($errors) ? json_encode($errors) : 'Unknown error updating FAQ.';
            throw new Exception("Failed to update FAQ: " . $error_msg);
        }

        return array('faq_id' => $faq->getId());
    }

    public static function deleteFaq($payload, $staff_id) {
        if (!isset($payload['faq_id'])) {
            throw new Exception("FAQ ID ('faq_id') is required.");
        }

        $faq = FAQ::lookup($payload['faq_id']);
        if (!$faq) {
            throw new Exception("FAQ not found.");
        }

        if (!$faq->delete()) {
            throw new Exception("Failed to delete FAQ.");
        }

        return array('success' => true);
    }

    // ── Canned Responses ───────────────────────────────────────────────────

    public static function createCanned($payload, $staff_id) {
        if (!isset($payload['title']) || !isset($payload['response'])) {
            throw new Exception("title and response are required.");
        }

        $vars = array(
            'title' => $payload['title'],
            'response' => $payload['response'],
            'dept_id' => isset($payload['dept_id']) ? $payload['dept_id'] : 0,
            'isenabled' => isset($payload['isenabled']) ? (int)$payload['isenabled'] : 1,
            'notes' => isset($payload['notes']) ? $payload['notes'] : ''
        );

        $errors = array();
        $canned = Canned::create($vars, $errors);
        
        if (!$canned || !empty($errors)) {
            $error_msg = !empty($errors) ? json_encode($errors) : 'Unknown error creating Canned Response.';
            throw new Exception("Failed to create Canned Response: " . $error_msg);
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

        $vars = $payload;
        $errors = array();
        
        if (!$canned->update($vars, $errors)) {
            $error_msg = !empty($errors) ? json_encode($errors) : 'Unknown error updating Canned Response.';
            throw new Exception("Failed to update Canned Response: " . $error_msg);
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
